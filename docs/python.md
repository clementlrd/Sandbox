# Managed Python environments and cache reuse

## Everyday use

Inside a Python runtime with `venv_volume = true`:

```bash
sandbox-py add requests
sandbox-py add --dev pytest
sandbox-py run python train.py
sandbox-py run pytest
sandbox-py status
```

`run` synchronizes automatically. There is no activation, environment name,
backend suffix or command ordering to remember. Run from the project directory,
or specify `--project PATH` **before** the command to run. A `pyproject.toml` is
required; automatic ancestor/workspace discovery is not supported yet.

Extras/groups are explicit and remembered for subsequent commands:

```bash
sandbox-py sync --extra training --group dev
sandbox-py run pytest
sandbox-py sync --no-extras --no-groups  # clear saved selections
```

Without an explicit selection, development groups are not installed. Repeat
`--extra` / `--group` for multiple selections.
`add --dev` / `add --group NAME` adds to and enables that dependency group;
`add --optional NAME` adds to and enables a project extra.

## Runtime protection

The pinned image, not live GPU detection, determines the Python stack. CUDA/XPU
images record `/opt/sandbox/python.json` during their build. The helper checks
the manifest against the base interpreter without importing GPU code. It
protects **all distributions visible to `/opt/venv/bin/python`**, not just torch.
Older/custom images without a manifest are inventoried on use.

The project environment inherits that stack through a `.pth` file. Runtime
packages are not copied, downloaded or installed into the project environment.
The resolver uses metadata-only representations of base packages as direct-URL
**constraints**, not version overrides. Incompatible requirements fail rather
than being silently changed. Internal metadata-only wheels are never installed
or published.

A project requiring `torch>=3` cannot run against an image providing torch
2.9.1. A different direct torch wheel is rejected even if its version matches.
Choose a compatible runtime or change the requirement deliberately. Missing GPU
visibility never silently switches dependencies to CPU builds.

Generic runtimes without `/opt/venv` use their system interpreter but do not
inherit/protect system packages. They must provide uv and Python 3.11+.

These checks validate **Python package metadata**, not every native ABI or CUDA
kernel. Wheels with undeclared system-library requirements can still be
incompatible. Real GPU execution needs testing on the target hardware.

## Resolution and recoverable updates

- **With `uv.lock`:** export with `--locked`, resolve against the runtime, and
  install non-runtime packages preserving any lockfile hashes (some local/flat
  sources have none). Stale/incompatible
  locks fail. `sync` never updates the project lock.
- **Without `uv.lock`:** resolve `pyproject.toml` against the runtime and keep the
  concrete resolution privately in the environment. No universal lockfile with
  private runtime paths is written into the repository. Existing versions are
  preferred when dependencies change; unrelated packages are not eagerly upgraded.
- **`add`:** uv edits staged project files, then the helper synchronizes. Existing
  lockfiles are updated under exact runtime version constraints; those versions
  must be obtainable from the project's indexes. Without a lockfile, only the
  project requirements are edited. Failed installation restores those edits
  unless another editor changed the files in the meantime.

Updates create an environment at its final generation path, validate the
combined runtime/project dependency metadata offline, then atomically switch a
`current` symlink. Virtual environments themselves are never moved. A failed
installation leaves the previous environment selected. `add` is not a crash-safe
multi-file transaction: after power loss/SIGKILL, inspect project files.

Each project has a cross-process lock. Managed runs hold generation leases so
later updates do not remove their environment while in use. Updates retain the
previous generation and prune older, unused generations. Processes launched
outside `sandbox-py run` do not have that protection.

`run` fingerprints dependency metadata and validates installed distribution
metadata. Unmanaged package additions/replacements are rejected. Recover with:

```bash
sandbox-py sync --rebuild
```

This guards ordinary mistakes, **not malicious commands inside the sandbox**.
It does not hash every installed source file or intercept arbitrary pip/uv/Python
calls. Do not edit installed files in place: eligible files are hardlinked to
the private uv cache.

## Storage

| Data | Storage and sharing |
| --- | --- |
| Runtime/PyTorch | Docker layers; inherited directly |
| Writable uv cache | `/opt/venvs/.uv-cache`, private per sandbox/image |
| Environments | `/opt/venvs/.managed/PROJECT_KEY/env-*`, same filesystem as cache |
| Trusted wheels | Host `.../sandbox/python-wheels`, read-only in containers |
| Docker build cache | BuildKit-managed; separate from runtime uv caches |

The launcher sets `UV_CACHE_DIR` and `UV_LINK_MODE=hardlink` for runtimes with
environment persistence. Eligible files are shared between private cache and
generations, **not** between mutually writable sandboxes. Extracted packages can
still occupy storage in each sandbox.

Inspect with `sandbox python-cache info`, `sandbox-py status`,
`docker system df -v`, and `docker buildx du`. `uv cache prune` inside a container
prunes its private cache using uv's locks; it does not delete environments or
host artifacts. Never edit uv's cache manually. `destroy` removes that sandbox's
recorded volumes, but leaves host wheels intact.

## Reuse trusted wheels across containers

This iteration offers **explicit host publication**, not automatic collection
of packages installed by sandbox code:

```bash
# Host: only publish wheels you trust and know were built for this image.
sandbox python-cache publish --runtime python-cuda /path/to/package.whl
sandbox python-cache info
```

Publication selects the runtime's currently tagged immutable image ID. New
containers pinned to that exact image automatically receive its wheel directory
read-only, and managed uv commands consult it. Sandboxes pinned to older images
do not receive the new image's artifacts. No suffix/profile ID needs to be typed.

Identical bytes share one content-addressed host object, even across profiles.
Replacing a filename with different contents is refused. Basic wheel metadata
is validated; **native compatibility is the publisher's responsibility**. Never
automatically publish unreviewed sandbox output.

The store supplies additional candidates; it does not force a wheel over other
versions. Project pins, sources, hashes, and runtime constraints still apply.
Compatible host environments can consume the profile path printed at publication
using uv/pip `--find-links`. Do not indiscriminately combine native profiles.

## Proposed next step: install once, reuse compatible builds

**Design note only: automatic building and publication below are not implemented.**
The goal is to keep one everyday workflow, including for native dependencies:

```bash
# Host: choose the environment, not individual CUDA package suffixes.
sandbox run experiment --runtime python-cuda

# Inside the sandbox, from a project with pyproject.toml:
sandbox-py add requests
sandbox-py add --dev pytest
sandbox-py add flash-attn              # proposed automatic native-build support
sandbox-py run python train.py
```

For an installation, the proposed manager would:

1. Read the pinned runtime contract and resolve the project without replacing
   runtime-provided packages. GPU visibility would remain a diagnostic, not a
   reason to silently switch dependency backends.
2. Reuse an approved compatible wheel when available. Native build identity must
   include the source identity, Python ABI, architecture, runtime image, relevant
   PyTorch/CUDA versions, build dependencies and build options. Initially, exact
   image IDs would be the conservative reuse boundary.
3. If no suitable wheel exists and building is authorized, use an isolated
   builder for the declared target. Missing toolchains or incompatible requirements
   should produce an actionable error, not an untracked fallback build.
4. Record the artifact's hash and build provenance. A trusted host-side service
   would control approval/publication; ordinary sandbox code must not gain write
   access to shared artifacts or automatically become trusted by other sandboxes.
   Review/consent policy still needs to be designed.
5. Install through uv into a replacement environment, validate it, and switch only
   after success. Later sandboxes using the same approved build profile would
   reuse the artifact without repeating its build.

Users should not need to capture installation calls, name wheel suffixes, copy
wheel files, select cache keys, or remember a separate build/publish/install order.
Publication policy would be configured once, with explicit consent when needed.
An incompatible request such as `sandbox-py add 'torch>=3'` against a torch 2.x
runtime should still fail and preserve the working environment.

Until this exists, source-only dependencies require a separately built trusted
wheel and explicit host publication, or a custom runtime image. Reusing wheel
artifacts avoids repeated downloads/builds; it does not by itself deduplicate all
extracted installations across the host and containers. Cross-host/runtime reuse
beyond the recorded compatibility boundary and storage cleanup need separate work.

## Current limits

- Automatic third-party source builds/native-build publication are not yet
  implemented. Source-only dependencies fail with `--no-build`; supply a trusted
  wheel or bake the dependency into a custom runtime.
- The current project may be installed editable when it declares a build backend.
  Its build is not cached; isolation may fetch backend dependencies. Dynamic or
  local-directory dependency projects can fail under the no-build policy.
- uv workspaces are explicitly rejected. Requirements-only projects still use
  the legacy named interface below.
- A shared package index/proxy and BuildKit artifact integration are future work.
  Containers do not mount the host's writable uv cache.
- The host store has inspection/publication, not automated garbage collection or
  a build service. Default uninstall preserves it; purge deletes it.

## Upgrade and compatibility

Reinstall the host helper and explicitly rebuild the runtime:

```bash
./install.sh --no-tools
sandbox build python-cuda             # or base / python-xpu
```

Existing sandboxes keep their pinned images. Create a new sandbox to adopt a
rebuilt image. Existing environments and pins are not migrated/deleted. Bundled
images and uv integration tests pin uv 0.9.7.

The original commands remain unchanged, separate from managed uv environments:

```bash
sandbox-py init scratch --empty
sandbox-py install scratch -r requirements.txt
sandbox-py check scratch
/opt/venvs/scratch/bin/python script.py
```

They use pip, ignore `uv.lock`, update in place, and only protect the original
torch inheritance. Prefer `add` / `sync` / `run` for new projects.
