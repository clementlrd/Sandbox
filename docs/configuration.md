# Configuration reference

User settings are read from `${XDG_CONFIG_HOME:-$HOME/.config}/sandbox/config.toml`. Existing files are **not overwritten** on reinstall. Restart the application container to pick up changes; named sandbox repositories and volumes persist across runs.

## Sandbox-wide settings

```toml
[sandbox]
memory = "8g"              # Docker memory limit
pids_limit = 512           # maximum container processes
network = "bridge"        # "bridge" or "none"
shared_tools = true        # host tools -> /opt/tools, read-only
venv_volume = true        # default for built-in base/python-xpu profiles
forward_env = []          # names of host env vars to forward, e.g. ["MY_API_KEY"]
gpu_devices = []          # legacy default for python-xpu, if not overridden
```

Never forward credentials unnecessarily. If a key is forwarded into a container, processes in that container may read it. Bridge networking means outbound network access is allowed; it is not limited to HTTP GET and does not prevent data uploads.

## Built-in images

```toml
[images]
preparer = "sandbox/preparer:v1"
base = "sandbox/base:v1"
python-xpu = "sandbox/python-xpu:v1"
```

`preparer` is the separate, network-disabled preparation container, *not* a runtime profile. Existing installations may keep their `[images]` block. OpenCode is now a standalone executable mounted from the tools directory, not an image/profile.

## Custom runtimes

```toml
[runtimes.go]
image = "local/sandbox-go:1.25"
shell = "/bin/bash"
venv_volume = false
devices = []
dockerfile = "~/.config/sandbox/runtimes/go/Dockerfile"
context = "~/.config/sandbox/runtimes/go"
```

The runtime table is optional. Runtime names use lower-case letters, digits and hyphens. `image` is mandatory for new runtimes. `shell` defaults to `/bin/bash`; `venv_volume` defaults to **false** for new runtimes; `devices` defaults to empty. `dockerfile` and `context` are only needed for `sandbox build NAME`; a runtime can instead point at a previously built or pulled image. If context is omitted, the Dockerfile's parent is used. Relative file paths resolve from the sandbox configuration directory.

For the two built-ins, `image` defaults to their `[images]` tags and `venv_volume` inherits the old global default. The built-in `python-xpu` device list inherits the legacy `sandbox.gpu_devices` setting, but `[runtimes.python-xpu].devices` overrides it.

`sandbox runtimes` lists the available profiles. `sandbox run NAME --runtime PROFILE` selects one for the command; there is no concept of a permanently active runtime. The image ID is pinned in the sandbox metadata on first run. See [Runtimes](runtimes.md).

## Generic read-only mounts

```toml
[[mounts]]
source = "~/.agents/skills"
target = "/root/.agents/skills"
optional = true

[[mounts]]
source = "~/.config/sandbox/apps/opencode.jsonc"
target = "/root/.config/opencode/opencode.jsonc"
optional = true
```

All configured mounts are **read-only** and available to *every* selected runtime. `optional = true` means skip if the host path does not exist; otherwise missing paths are errors. The current implementation has **no `scan` configuration property**. In anonymized sandboxes, user-configured mounts are included in full denylist scanning, while the built-in trusted host-tools mount is excluded to avoid false positive matches in compiled binaries.

Do not mount SSH keys, private Git credential directories, Docker sockets or arbitrary host paths into an agent environment. The mount system is deliberately restricted from replacing `/workspace`, `/opt/tools` and `/opt/venvs`.

## Setup recipes

```toml
[setup]
recipes_dir = "~/.config/sandbox/recipes"
confirm_host_execution = true
```

Recipes are executable scripts stored on the **host**, with the target sandbox name as `$1`. They may call `sandbox exec NAME --runtime RUNTIME -- COMMAND` and read host credentials, so only run scripts you trust. The system prompts before executing them, unless explicitly confirmed via `--yes` or `confirm_host_execution = false`.

```bash
sandbox setup list
sandbox setup agent opencode-openai --runtime base
sandbox setup agent opencode-openai --force  # reapply on this sandbox
sandbox run agent --setup opencode-openai
```

Completion is tracked per **sandbox+recipe name**, *not* per runtime. This is appropriate for app credentials shared across runtimes, but a recipe that mutates runtime-specific Python venvs must be forced/reapplied as necessary. Recipes run only when explicitly requested, not automatically at every startup.

## Persisted paths and configuration behavior

| Path | Origin | Scope |
| --- | --- | --- |
| `/workspace` | Host-side independent clone | Named sandbox |
| `/root/.local/share` | Docker named volume | Named sandbox |
| `/root/.config` | Docker named volume | Named sandbox |
| `/root/.cache` | Docker named volume | Named sandbox |
| `/opt/venvs` | Docker named volume, if enabled | Named sandbox + **image ID** |
| `/opt/tools` | Read-only host bind | Shared globally |
| `[[mounts]]` targets | Read-only host binds | Selected for all runtimes |

Configuration files mounted via `[[mounts]]` can shadow individual paths within the persistent config volume. Don't try to edit a read-only mounted template from inside the container.

## Legacy fields

`[sandbox].mount_global_skills` and `[sandbox].opencode_config` are still accepted and mapped to equivalent optional read-only mounts. Obsolete `[images].opencode` and `[images].opencode-xpu` are ignored. New installations should use generic `[[mounts]]` and the shared tools directory.
