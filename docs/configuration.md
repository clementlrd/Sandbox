# Configuration reference

Configuration is read from `${XDG_CONFIG_HOME:-$HOME/.config}/sandbox/config.toml`. The installer creates it only when absent. The configuration format is **strict**: unknown tables and keys are rejected. See [the example](../examples/config.toml) for all supported settings.

See [the complete example](../examples/config.toml).

## Global sandbox settings

```toml
[sandbox]
memory = "8g"
pids_limit = 512
network = "bridge"               # or "none"
shared_tools = true                # $XDG_DATA_HOME/sandbox/tools -> /opt/tools (RO)
forward_env = []                  # explicit host env names; values are accessible inside
# Name must exist under [runtimes.NAME]:
default_runtime = "base"
```

`--runtime NAME` overrides `default_runtime` for a single `run`, `exec`, or `setup`. Nothing becomes a permanently active runtime. Network `bridge` does not limit traffic to GET requests; container commands can make outbound connections. The image must be compatible with the host architecture and rootless Docker.

## Trusted Git preparer image

```toml
[preparer]
image = "sandbox/preparer:v1"
```

This is not an application runtime. `sandbox build preparer` builds its packaged Dockerfile, and `sandbox create` uses the resulting local image ID with `--network none`. The preparer may read the original repository during creation; the application runtime cannot.

## Explicit runtime profiles

**Every runtime is declared explicitly**, even bundled examples. There is no automatic or special-case `base`/`python-xpu` selection.

```toml
[runtimes.base]
image = "sandbox/base:v1"
shell = "/bin/bash"
venv_volume = true
devices = []
dockerfile = "bundled:images/base/Dockerfile"
context = "bundled:."

[runtimes.python-xpu]
image = "sandbox/python-xpu:v1"
shell = "/bin/bash"
venv_volume = true
devices = ["/dev/dri/renderD128"]
dockerfile = "bundled:images/python-xpu/Dockerfile"
context = "bundled:."
```

Runtime names are lower-case and may contain digits and hyphens. Only `image` is required; defaults are `shell = "/bin/bash"`, `devices = []`, `venv_volume = false`, `dockerfile = null` and `context = null`. User-supplied profiles need no changes to sandbox code:

```toml
[runtimes.node]
image = "node:22-bookworm"
shell = "/bin/bash"
venv_volume = false
```

For local builds, set `dockerfile` and optionally `context`. Ordinary relative paths are relative to the sandbox configuration directory. A `bundled:` path resolves under the installed `${XDG_DATA_HOME:-$HOME/.local/share}/sandbox` resources; it cannot escape that directory. A runtime without a Dockerfile must be built or pulled separately. No builds run implicitly. See [Runtime guide](runtimes.md).

Devices are passed via Docker `--device` with their numeric host groups. This does not guarantee access under rootless Docker; check ACLs and user namespace mappings. Do not expose a Docker socket or unnecessary devices.

The `/opt/venvs` volume is created only if `venv_volume = true` and is keyed to both the named sandbox and the **image ID**. Application data/config/cache volumes are per sandbox across all runtimes. The repository is per sandbox. Shared tools are read-only and global.

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

There are no OpenCode-specific mount flags. All declared mounts are exposed to *every runtime* and included in full denylist scans for anonymized sandboxes. `optional = true` skips missing sources; otherwise missing sources fail. The host-managed `/opt/tools` mount is exempt from binary content scans, as documented in [Security](security.md). No `scan` property is implemented. The launcher refuses mounts overlapping `/workspace`, `/opt/tools` or `/opt/venvs`.

## Trusted setup recipes

```toml
[setup]
recipes_dir = "~/.config/sandbox/recipes"
confirm_host_execution = true
```

Recipes are executable trusted host scripts. Use `sandbox setup NAME RECIPE` or `sandbox run NAME --setup RECIPE`. Completion is tracked per sandbox/recipe, not per runtime; reapply with `--force`. Recipes can access your host credentials, so review them before execution.

## Persistence at a glance

| Container location | Host backing | Scope |
| --- | --- | --- |
| `/workspace` | Independent Git clone under XDG state | Sandbox |
| `/root/.local/share` | Named volume (`app-data`) | Sandbox |
| `/root/.config` | Named volume (`app-config`) | Sandbox |
| `/root/.cache` | Named volume (`app-cache`) | Sandbox |
| `/opt/venvs` | Named volume, when enabled | Sandbox + image ID |
| `/opt/tools` | Host read-only bind | Shared |
| `[[mounts]]` | Host read-only binds | Per configured mount |

The configuration is read from the host at each execution; it is not copied into named sandboxes. Runtimes use their pinned image IDs and persistent volumes across application-container restarts.
