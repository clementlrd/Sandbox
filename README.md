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

The project contains small standalone scripts at `tools/install-opencode.sh` and `tools/install-jq.sh`; they are also copied into `~/.local/share/sandbox/tool-installers/` for later manual use:

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
~/.local/share/sandbox/tools/bin/        # Shared executable files
~/.config/sandbox/config.toml            # User configuration
~/.config/sandbox/denylist.txt           # Sensitive identifiers (600)
~/.config/sandbox/apps/opencode.jsonc    # Optional OpenCode config
```

`install.sh` does **not** overwrite existing config or denylist files. It installs `.dockerignore` if present, or generates a safe default when missing. Upgrading from v0.1 is supported; existing `[sandbox]` settings `mount_global_skills` / `opencode_config` and old `opencode*` image tags are accepted for compatibility, but no application images are built.

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

### Private denylist

Edit `~/.config/sandbox/denylist.txt` with one literal term per line, UTF-8, case-insensitive. Keep file mode `600`. At least one active term is required for `--anonymize`.

```text
Jane Example
jane@example.com
my-personal-username
private.example.com
```

Before launch, the trusted preparer scans Git objects (including unreachable objects), current workspace files and paths, symlink targets, Git metadata, and all configured read-only mounts—including shared tools. The denylist never enters the runtime container. Matches block the session. **This is a best-effort privacy check**, not a guarantee: compressed or encoded text, unexpected identifiers, and sensitive contents in writable app volumes might not be caught. Large tool binaries can make anonymized preflights slower; this is the cost of including every mounted read-only path in the scan.

### Intel XPU Python environments

The Intel upstream base image places PyTorch under `/opt/venv`, *not* the system Python. To make a persistent environment that reuses that preinstalled PyTorch:

```bash
sandbox run dev --runtime python-xpu
sandbox-venv experiment1
source /opt/venvs/experiment1/bin/activate
python -c 'import torch; print(torch.xpu.is_available())'
python -m pip install transformers
```

`sandbox-venv` arranges a `.pth` reference to Intel's shared packages; plain `python -m venv --system-site-packages` does not automatically inherit `/opt/venv` packages. GPU access needs the host render-device permissions to work with rootless Docker. The venv volume is bound to the pinned runtime image ID to reduce breakage after Python upgrades.

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
examples/config.toml             # generic mount configuration
install.sh                        # optional interactive tool selection
```
