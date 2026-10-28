# One-time migration from the pre-public-preview configuration

This is a **breaking configuration cleanup**, intended for early users of earlier `sandbox` archives. There is no compatibility layer in the new CLI. **You do not normally need to destroy existing named sandboxes or their volumes.** Do these steps before installing/using the new CLI.

## 1. Back up and inspect existing state

```bash
sandbox list
# For each important sandbox, consider importing committed work first:
# sandbox import NAME
cp -p ~/.config/sandbox/config.toml ~/.config/sandbox/config.toml.before-cleanup
# Backup any customized host-side recipes and the denylist if desired.
docker volume ls --filter 'name=sandbox-'
```

Check `sandbox inspect NAME` for recorded volumes. If a `sandbox-opencode-<id>` legacy volume is listed, see the *Legacy OpenCode volume* section below before upgrading. Most recent installations have `sandbox-app-data-*` and need no volume migration.

Do not prune Docker volumes or images, and do not delete `$XDG_STATE_HOME/sandbox/instances` until you have reviewed/imported outstanding work. Original source repositories are not affected by migration.

## 2. Change `config.toml` manually

The safest path is to copy the **new** [`examples/config.toml`](../examples/config.toml) to a temporary file, compare with your backup, and transfer custom settings, devices and mounts. The installer does **not** overwrite your config.

Required changes:

| Before | Now |
| --- | --- |
| `[images].preparer` | `[preparer].image` |
| `[images].base` / `[images].python-xpu` | `[runtimes.base].image` / `[runtimes.python-xpu].image` |
| `[sandbox].gpu_devices` | `devices` inside the appropriate `[runtimes.NAME]` |
| `[sandbox].venv_volume` | `venv_volume` inside each `[runtimes.NAME]` |
| `[sandbox].mount_global_skills` | Explicit `[[mounts]]` for each skills directory |
| `[sandbox].opencode_config` | Explicit `[[mounts]]` for the config file |
| `[images].opencode` / `[images].opencode-xpu` | **Delete**; OpenCode is a shared executable, not a runtime image |
| No default runtime key | `[sandbox].default_runtime = "base"` (or your preferred profile) |
| Implicit built-in Dockerfiles | `dockerfile = "bundled:images/NAME/Dockerfile"`, `context = "bundled:."` |

Example minimal CPU-only configuration:

```toml
[sandbox]
default_runtime = "base"
shared_tools = true

[preparer]
image = "sandbox/preparer:v1"

[runtimes.base]
image = "sandbox/base:v1"
shell = "/bin/bash"
venv_volume = true
devices = []
dockerfile = "bundled:images/base/Dockerfile"
context = "bundled:."

[setup]
recipes_dir = "~/.config/sandbox/recipes"
confirm_host_execution = true
```

Add `[[mounts]]` for your skills/OpenCode template, and a `[runtimes.python-xpu]` block if needed. `sandbox` is not tied to those applications or runtimes; any valid `[runtimes.NAME]` is supported. **Remove the old `[images]` section entirely.** Unrecognized old keys are now errors by design.

## 3. Handle legacy OpenCode volumes, if present

Most recent setups already use `sandbox-app-data-<id>` for OpenCode sessions/auth. The only exceptional legacy case is a recorded `sandbox-opencode-<id>` that used to be mounted over `/root/.local/share/opencode`.

Before upgrading, while the old CLI still works, **check which volume holds your credentials**. Back up the data or export credentials via the host recipe, and import them again after upgrading if necessary. For a volume-to-volume copy (with no application container running), you can use a temporary container with the volumes mounted under different directories:

```bash
# Replace <id> with the id from 'sandbox inspect NAME'.
# Review paths first and ensure no sandbox process is using these volumes.
docker run --rm --network none \
  --mount type=volume,src=sandbox-opencode-<id>,dst=/old,readonly \
  --mount type=volume,src=sandbox-app-data-<id>,dst=/new \
  --entrypoint /bin/sh sandbox/base:v1 -c \
  'mkdir -p /new/opencode && cp -a /old/. /new/opencode/'
```

This optional procedure copies into the regular app-data volume. **Verify the restored OpenCode login and sessions before deleting any legacy volume.** The new runtime will not attach the old volume. If no legacy `sandbox-opencode-*` volume exists, skip this step.

## 4. Apply the patch, install and verify

```bash
# From the source project directory:
git apply --check /path/to/sandbox-clean-architecture.patch
git apply /path/to/sandbox-clean-architecture.patch
./install.sh --no-tools
sandbox runtimes
sandbox list
sandbox run NAME --runtime base
```

The installer now **replaces** its managed Python helper code and Docker build resource directories, clearing obsolete files such as old OpenCode image recipes, but preserving your config, shared tools, state, and volumes. Existing runtime image IDs remain pinned in saved metadata; no Docker rebuild is needed merely for this cleanup.

Old OpenCode Docker tags and BuildKit cache are **not** deleted by the installer; only remove them after confirming saved sandboxes do not depend on those image IDs.

## 5. Uninstall safely

See [Uninstall](uninstall.md). `./uninstall.sh` removes the installed program without touching saved sandboxes, their Docker volumes, your config, or shared OpenCode and jq binaries. `--purge-data` requires that all named sandboxes have been destroyed first.
