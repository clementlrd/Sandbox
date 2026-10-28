# sandbox

A small Linux tool for **named, independent Git sandboxes using rootless Docker**. A sandbox is a prepared repository plus persistent volumes, not an application profile. Run a shell, start OpenCode, execute tests, or use any other executable available in the runtime or shared tools directory.

`create --anonymize` rewrites Git history inside a dedicated trusted *preparer* container, and a mandatory denylist preflight checks the result before every runtime launch. The original repository, commit map, denylist, host SSH keys, and Docker socket are **not mounted into the application container**. Git changes can be imported to host review branches without pushing or merging automatically.

> **Status:** Tested with local Git/unit tests and shell installer tests. Docker builds, rootless-device access, and the OpenCode executable must be verified on your machine; they could not be exercised in the packaging environment.

## 1. Requirements (Arch Linux)

- Python >= 3.11, Git, Docker CLI with buildx/BuildKit, and **rootless Docker daemon**. Rootful Docker is rejected by the CLI.
- `curl`, `tar`, `sha256sum` for installing optional standalone tools on the host.
- Intel GPU userspace and a working `/dev/dri/renderD*` device if using the XPU runtime.
- Sufficient storage for Intel's multi-GB PyTorch XPU image (only if you need it).

For example:

```bash
sudo pacman -S --needed docker docker-buildx git python curl tar coreutils
```

Set up a rootless daemon via Arch's `docker-rootless-extras`, configure subordinate UID/GID ranges, start its user service/socket, and select the rootless context. Exact setup depends on your system; see [Docker rootless](https://docs.docker.com/engine/security/rootless/) and [ArchWiki Docker](https://wiki.archlinux.org/title/Docker).

```bash
systemctl --user enable --now docker.socket
# Only if you don't already have this context:
docker context create rootless --docker "host=unix:///run/user/$(id -u)/docker.sock"
docker context use rootless
docker info --format '{{json .SecurityOptions}}'  # Must include "rootless"
```

## 2. Install and select optional tools

From the extracted `sandbox-project/` directory:

```bash
./install.sh
```

If stdin is a terminal, the installer prompts:

```text
Optional shared tools (installed on the host, mounted RO inside sandboxes):
  1) OpenCode standalone CLI
  2) jq
Enter numbers separated by spaces, "all", or "none" [none]:
```

Enter `1 2`, `all`, `1`, `2`, or `none`. In non-interactive installs it skips tools unless explicitly requested:

```bash
./install.sh --tools opencode,jq  # downloads BOTH
./install.sh --tools jq           # jq only
./install.sh --no-tools          # install CLI/resources without downloads
```

The project always installs **`tools/sandbox-py`** into the shared tools directory (no download required). The optional download scripts are `tools/install-opencode.sh` and `tools/install-jq.sh`; they are also copied into `~/.local/share/sandbox/tool-installers/` for later manual use:

```bash
~/.local/share/sandbox/tool-installers/install-opencode.sh
~/.local/share/sandbox/tool-installers/install-jq.sh
# Optional pin / checksum for OpenCode download:
OPENCODE_VERSION=2.0.0 OPENCODE_SHA256='<published tarball SHA256>' \
  ~/.local/share/sandbox/tool-installers/install-opencode.sh
```

OpenCode's installer uses the official standalone **Linux x86-64 glibc baseline** archive, defaulting to the vendor's `latest` endpoint. It warns when no checksum is supplied. `jq` installs the pinned **1.8.2** Linux amd64 release and verifies its published SHA256. Both installers currently target x86-64 Linux. They download on the **host**, not from inside Docker, and install atomically into the host tools directory. Inspect the installer scripts before use and supply a trusted OpenCode checksum if you need stronger supply-chain assurance.

Installed layout (XDG paths honored where applicable):

```text
~/.local/bin/sandbox                      # CLI
~/.local/libexec/sandbox/sandboxlib/     # Python helpers
~/.local/share/sandbox/images/          # Docker build resources
~/.local/share/sandbox/tool-installers/  # Host-side download scripts
~/.local/share/sandbox/tools/bin/        # sandbox-py (always), plus optional tools
~/.config/sandbox/config.toml            # User configuration
~/.config/sandbox/denylist.txt           # Sensitive identifiers (600)
~/.config/sandbox/apps/opencode.jsonc    # Optional OpenCode config
~/.config/sandbox/recipes/*.sh        # Trusted host-side setup recipes
```

`install.sh` does **not** overwrite existing config, denylist, or recipe files. It installs `.dockerignore` if present, or generates a safe default when missing. Upgrading from v0.1 is supported; existing `[sandbox]` settings `mount_global_skills` / `opencode_config` and old `opencode*` image tags are accepted for compatibility, but no application images are built.

## 3. Build runtime images (explicit and cached)

```bash
sandbox build preparer    # trusted git-filter-repo + preflight
sandbox build base        # Ubuntu 24.04, Git, Python, shell utilities
sandbox build python-xpu  # Intel PyTorch XPU image + utility layer (optional)
```

Image graph:

```text
python:3.12-slim-bookworm       -> sandbox/preparer:v1
ubuntu:24.04                   -> sandbox/base:v1
intel/pytorch:xpu-2.14.0-ubuntu24.04 -> sandbox/python-xpu:v1
```

**No OpenCode-specific images.** Installing OpenCode or `jq` in the host tools directory doesn't rebuild any image. Image builds use BuildKit and cache the `apt` package download directories. Rebuilding the runtime does not re-download the full upstream Intel image if Docker still retains its layers. The Git repository is *never* part of a Docker image build context. `create` and `run` never initiate a build.

Each sandbox pins the **exact image ID on first use** (per runtime profile). Rebuilding a tag doesn't silently change a saved sandbox. Use a new sandbox for intentional runtime upgrades. Use `docker system df -v` and `docker buildx du` to inspect storage; avoid indiscriminate pruning if fast XPU rebuilds matter.

## 4. Shared tools versus persistent state

The host directory `${XDG_DATA_HOME:-~/.local/share}/sandbox/tools` is mounted **read-only** as `/opt/tools`. `/opt/tools/bin` is **prepended to the image's existing `$PATH` at container startup**. This preserves `/opt/venv/bin` in the Intel image's PATH and does not change the Python version selected by the XPU runtime.

```text
Host                                              Container
~/.local/share/sandbox/tools/bin/opencode     ->  /opt/tools/bin/opencode (RO)
~/.local/share/sandbox/tools/bin/jq           ->  /opt/tools/bin/jq (RO)
~/.local/share/sandbox/tools/bin/sandbox-py   ->  /opt/tools/bin/sandbox-py (RO)
```

Applications launched inside a sandbox cannot modify the shared tools mount. Tool installers run on the host. There is no `sandbox tool`, `--app`, or `--tool-version` interface. The tools must be compatible with the runtime's architecture and libraries; the supplied binaries target Linux x86-64 and Ubuntu's glibc. Tools that need additional libraries may require a runtime image change.

Per-sandbox Docker named volumes persist these writable paths across `run` and `exec`:

| Path | Use |
|---|---|
| `/root/.local/share` | Application data, including OpenCode sessions/auth |
| `/root/.config` | Application configuration (with optional read-only overrides) |
| `/root/.cache` | Application caches |
| `/opt/venvs` | Python virtual environments, **per sandbox and pinned runtime image ID** |
| `/workspace` | Independent Git repository, bind-mounted read/write from sandbox state |

The `--rm` container filesystem outside these mounts disappears when the process exits. `apt install jq` inside a running container does **not** persist; using `/opt/tools/bin/jq` does. Installed Python packages inside `/opt/venvs/<name>` persist. **Security note:** the agent can read application credentials in its own volumes, and code on the network can exfiltrate data; this is not a no-exfiltration guarantee.

Old named sandboxes with a legacy OpenCode state volume continue mounting it at `/root/.local/share/opencode`, so their earlier sessions remain available. To reclaim old OpenCode image disk usage manually, inspect your current images and sandbox references before deleting anything.

## 5. Configuration and optional skill mounts

Edit `~/.config/sandbox/config.toml`. Example:

```toml
[sandbox]
memory = "8g"
pids_limit = 512
network = "bridge"
gpu_devices = ["/dev/dri/renderD128"]  # only for Intel GPU users
forward_env = []                # e.g. ["ANTHROPIC_API_KEY"] (exposed to runtime)
venv_volume = true
shared_tools = true

[images]
preparer = "sandbox/preparer:v1"
base = "sandbox/base:v1"
python-xpu = "sandbox/python-xpu:v1"

[[mounts]]
source = "~/.config/opencode/skills"
target = "/root/.config/opencode/skills"
optional = true

[[mounts]]
source = "~/.agents/skills"
target = "/root/.agents/skills"
optional = true

[[mounts]]
source = "~/.config/sandbox/apps/opencode.jsonc"
target = "/root/.config/opencode/opencode.jsonc"
optional = true
```

The optional `[setup]` section controls host-side recipes:

```toml
[setup]
recipes_dir = "~/.config/sandbox/recipes"
confirm_host_execution = true
```

Recipes are executable `.sh` files on your **host**, never inside the Git repository. They are installed into `~/.config/sandbox/recipes/` only when missing, so your edits survive upgrades. Setup requires an explicit command, and by default asks for confirmation before running the host script (use `--yes` for noninteractive workflows). Use `sandbox setup list` to discover recipes; files must be owned by you and not writable by group/others. These scripts are trusted programs with host privileges, so inspect them before using them.

The installed template also includes `~/.claude/skills`. Generic `[[mounts]]` are read-only in **every runtime container**, not only when OpenCode happens to run. They're scanned by the preflight in anonymized sandboxes. The host-managed shared tools directory is **not** scanned for denylist terms (to avoid matching arbitrary binary bytes), but is still mounted read-only. Only put trusted executables there; the agent can read their contents. You can mount other host configuration/skill files deliberately, but they may contain secrets. The host's entire home, SSH agent, Git config, and Docker socket are never mounted automatically. If upgrading from an older config, the previous skill and OpenCode config options remain honored even without `[[mounts]]`.

## 6. Sandbox workflow

```bash
# Build prerequisite once
sandbox build preparer
sandbox build base

# Ordinary independent Git workspace
sandbox create dev ~/projects/my-project

# Optional privacy rewrite: commit metadata anonymized, preflight mandatory
sandbox create agent ~/projects/my-project --anonymize

sandbox list
sandbox inspect agent

sandbox run agent                   # interactive /bin/bash, CPU runtime
sandbox run agent --runtime python-xpu
sandbox exec agent -- jq --version
sandbox exec agent --runtime python-xpu -- python -c 'import torch; print(torch.xpu.is_available())'

# In shell: opencode; jq; git; sandbox-venv experiment1
sandbox import agent                # host review branches only; no push/merge
sandbox destroy agent --yes         # removes workspace and recorded volumes
```

The source Git checkout must have no uncommitted or untracked changes when you call `create`. Preparation makes a fully independent clone, preserves branches and merge structure, removes remotes, and avoids shared Git object alternates. For anonymized sandboxes, `git-filter-repo` rewrites author, committer, and tagger names/emails to `Sandbox <sandbox@invalid>`. The host-only `commit-map` allows the importer to reconnect newly created commits to original ancestry and recreate multi-parent merges as host review branches with your host Git identity.

**Import caveats:** The agent should commit intended changes; a branch import only transfers committed objects. Tags, notes, submodules, Git LFS and unusual hooks need additional work. Anonymization changes commit IDs, so translation relies on the private map. The importer does **not** change the source checkout or push to a remote.

### Optional host-side setup recipes

For the OpenCode browser-login workflow, first authenticate the **host** installation of OpenCode using your normal browser (once):

```bash
~/.local/share/sandbox/tools/bin/opencode auth login openai
```

After creating a sandbox, import your host OpenCode OpenAI login into that sandbox's persistent application-data volume:

```bash
sandbox setup list
sandbox setup agent opencode-openai --runtime python-xpu
# Or run the recipe just before entering the shell:
sandbox run agent --runtime python-xpu --setup opencode-openai
```

The first successful execution records a completion marker for that sandbox. If you request the same recipe again, `sandbox` skips it unless you specify `--force` (standalone setup) or `--force-setup` (run shortcut). A credential revocation, lost volume, or changed host login requires re-running it manually:

```bash
sandbox setup agent opencode-openai --runtime python-xpu --force
```

Scripts are stored under `~/.config/sandbox/recipes/*.sh`, **outside any sandbox repository**, and executed on the Arch host after confirmation. `--yes` bypasses the confirmation prompt only; it does not force a repeated recipe. In a noninteractive shell, use `--yes`. For a new custom recipe, create a non-symlink `.sh` file in the recipes directory and `chmod 700` it. The script receives the sandbox name as `$1` and these environment variables:

- `SANDBOX_NAME` — the sandbox name.
- `SANDBOX_CLI` — absolute path to the installed sandbox CLI.
- `SANDBOX_TOOLS_DIR` — host shared tools directory.
- `SANDBOX_RUNTIME` — selected runtime (`base` or `python-xpu`).

The supplied `opencode-openai` recipe executes the host CLI's `auth export openai`, then pipes its stdout through `sandbox exec` to `opencode auth import` inside the sandbox. No secrets are written to your host repository or printed by the recipe; the importer uses a private temporary file *inside the ephemeral container*. Credentials then persist in the sandbox's app-data Docker volume and remain accessible to commands running inside that sandbox. **This is convenience, not credential isolation from the agent.**

The recipe's standalone `setup` command defaults to the `base` runtime; use `--runtime python-xpu` if you only built the XPU runtime. `run --setup` automatically uses the runtime selected for the run.

These recipes **never run automatically** on ordinary `sandbox run` or `sandbox create`. A success marker lives in host-side sandbox metadata; if you manually delete the authentication volume, use `--force` to repopulate it. Scripts that fail aren't marked completed. `sandbox destroy` removes the metadata marker together with the sandbox.

### Private denylist

Edit `~/.config/sandbox/denylist.txt` with one literal term per line, UTF-8, case-insensitive. Keep file mode `600`. At least one active term is required for `--anonymize`.

```text
Jane Example
jane@example.com
my-personal-username
private.example.com
```

Before launch, the trusted preparer scans Git objects (including unreachable objects), current workspace files and paths, symlink targets, Git metadata, and configured read-only skill/config mounts. **The built-in shared tools mount is excluded from binary-content scanning**, since host-installed executables may trigger spurious denylist matches. The denylist never enters the runtime container. Matches block the session. **This is a best-effort privacy check**, not a guarantee: compressed or encoded text, unexpected identifiers, and sensitive contents in writable app volumes might not be caught.

### Intel XPU Python environments

The Intel upstream image places GPU-enabled PyTorch under `/opt/venv`. The included `sandbox-py` helper creates environments under the persistent `/opt/venvs` Docker volume and makes those base Python packages available without copying them. It uses **pip**, not uv, for dependency resolution. Your `uv.lock` is not used or modified.

**Typical setup for a uv-created repository:**

```bash
sandbox run agent --runtime python-xpu
# Inside the container, in /workspace:
sandbox-py init project             # installs pyproject.toml dependencies
sandbox-py init project --group dev  # include optional PEP 735 dev group
sandbox-py install project transformers datasets
sandbox-py check project
/opt/venvs/project/bin/python train.py
```

For a project with an editable-installable build backend, `init` runs `pip install -e /workspace` with a PyTorch constraint. For **uv-style projects without `[build-system]`**, it reads `[project].dependencies` and, if requested, `[project.optional-dependencies]` / `[dependency-groups]` from `pyproject.toml` and passes the requirements to pip. **Pip still resolves the full transitive dependency graph**. Use `--extra visualization` and/or `--group dev` to include non-default dependencies. PEP 735 `include-group` entries are supported. This approach doesn't apply `uv.lock` or uv-specific `[tool.uv.sources]`, and a project lacking a build backend isn't installed as an editable package; your code still runs from `/workspace`.

The helper generates `/opt/venvs/project/torch-constraints.txt` from the version of PyTorch baked into the XPU runtime. Every `sandbox-py init` / `sandbox-py install` operation supplies that constraint. Pip normally **reuses installed compatible packages** and installs missing ones into the child environment. If the project requests an incompatible PyTorch version, the resolution **fails rather than replacing the inherited GPU build**. Other installed packages may be shadowed when their versions conflict; only PyTorch is pinned. It also checks that PyTorch has not been shadowed by a project-local install.

`/opt/venvs/project` survives closing the ephemeral container, but **`sandbox destroy` deletes its volume**. You can always use `/opt/venvs/project/bin/python` without activating the environment. For interactive use, `source /opt/venvs/project/bin/activate` works too. Re-run `sandbox-py init project` after changing dependencies, or install additional packages with `sandbox-py install project PACKAGE...`.

`tools/sandbox-py` is always installed by `./install.sh --no-tools`, independently of OpenCode and jq; **no Docker rebuild is needed**. The XPU image's existing `sandbox-venv` remains available as a low-level helper.

**Protection scope:** `sandbox-py` avoids accidentally changing/overriding image PyTorch through its own pip commands. It is **not** a security boundary: an agent may invoke pip or uv directly, overwrite files in its writable venv, or install a local conflicting dependency. The image's original PyTorch is still restored on a fresh `docker run --rm` container. If a child venv is damaged, delete/recreate that child environment. The interpreter and its shared packages are tied to the pinned runtime image ID; don't copy venvs between different base-image versions.

## 7. Limitations and troubleshooting

- Docker containers share the host kernel. Rootless mode narrows privilege but is not equivalent to a VM.
- No authenticated Git push is available by default because host SSH credentials and Git remotes aren't supplied; unrestricted networking still permits arbitrary outbound uploads.
- Trusted preparation has a *read-only* original repo mount and **no network**. Runtime has outbound network unless set to `none`.
- The runtime filesystem outside configured mounts is ephemeral. For OS tools, add them to the base image; portable standalone tools belong in the shared tools directory.
- The shared tools directory is not a secure executable allowlist. An agent can download its own binaries into writable paths.
- A sandbox is persistent across runs, but concurrent launches sharing the same Git checkout and app-state volumes aren't coordinated; avoid concurrent Git edits.
- Mounting an optional OpenCode configuration read-only means OpenCode cannot modify that particular file, while other per-sandbox configuration remains writable.
- Source LFS objects, sparse checkouts, and linked worktrees may require enhancements.

Run local tests with:

```bash
python -m unittest discover -s tests -v
bash -n install.sh tools/*.sh
python -m py_compile tools/sandbox-py
```

If your pre-upgrade configuration still includes `opencode` or `opencode-xpu` image tags, they are safely ignored; build only `preparer`, `base`, and `python-xpu` from now on. If a reinstalled `sandbox` command still asks for `--app`, verify `~/.local/bin` is first in `$PATH`.

### Project layout

```text
bin/sandbox                       # user-facing CLI
libexec/sandboxlib/cli.py         # lifecycle, runtime, Docker commands
libexec/sandboxlib/prepworker.py  # trusted repository preparation
libexec/sandboxlib/preflight.py   # mandatory preflight for --anonymize
libexec/sandboxlib/importer.py    # commit map / host review branch import
images/{preparer,base,python-xpu}/ # minimal runtime image family
tools/install-opencode.sh         # official standalone OpenCode archive
tools/install-jq.sh               # pinned & checksum-verified jq
tools/sandbox-py                   # persistent Python venvs + pip constraints
recipes/opencode-openai.sh         # trusted host browser-login credential transfer
examples/config.toml             # generic mount configuration
install.sh                        # optional interactive tool selection
```
