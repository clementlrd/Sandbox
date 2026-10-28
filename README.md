# sandbox

A small, explicit **named Git sandbox manager** for Linux, built on **rootless Docker**. Optional privacy rewriting is performed in a **separate trusted preparation container**; the runtime container never receives the source Git repository, anonymization map, or denylist. OpenCode 2 is an optional application, not part of the core sandbox abstraction.

This tool does **not** build images during `create` or `run`, does **not** expose the host's SSH agent or Docker socket, and never merges or pushes into your original repository automatically.

> Status: source project with locally tested Git logic. Docker image builds, the actual rootless daemon workflow, and Intel GPU access **have not been end-to-end tested in the packaging environment**. Test with a throwaway repository first.

## 1. Requirements (Arch Linux)

- Linux (developed with Arch in mind), **Python >= 3.11**, Git, Docker CLI with **BuildKit** / buildx.
- A **rootless Docker daemon** and a selected rootless Docker context. Ordinary rootful Docker is deliberately rejected.
- Sufficient disk space for Intel's PyTorch XPU image (multi-GB) if enabled.
- For XPU: a supported Intel GPU with working host drivers and an accessible `/dev/dri/renderD*` device.
- Python's standard library only for the `sandbox` command. `git-filter-repo` is installed **inside the preparer image**, not required on the host.

Install the ordinary packages:

```bash
sudo pacman -S --needed docker docker-buildx git python
```

Arch's rootless Docker packaging is separate: install `docker-rootless-extras` from the AUR, configure **at least 65,536 subordinate UIDs and GIDs** for your user in `/etc/subuid` and `/etc/subgid`, then start the user service or socket:

```bash
systemctl --user enable --now docker.socket

docker context create rootless \
  --docker "host=unix:///run/user/$(id -u)/docker.sock"
docker context use rootless

docker info --format '{{json .SecurityOptions}}'
# Must contain "rootless"
```

If the context already exists, just select it; don't recreate it. For Arch-specific guidance see [ArchWiki Docker](https://wiki.archlinux.org/title/Docker) and [Docker Rootless mode](https://docs.docker.com/engine/security/rootless/). Resource limits require appropriate cgroup v2 delegation. The kernel, render permissions, and rootless daemon are host setup, not automated by this installer.

## 2. Install CLI and resources

```bash
unzip sandbox.zip
cd sandbox-project
./install.sh

# Ensure ~/.local/bin is in PATH
sandbox --help
```

The installer copies:

```text
~/.local/bin/sandbox
~/.local/libexec/sandbox/sandboxlib/*.py
~/.local/share/sandbox/images/**
~/.local/share/sandbox/libexec/sandboxlib/**
~/.config/sandbox/config.toml
~/.config/sandbox/denylist.txt
~/.config/sandbox/apps/opencode.jsonc
```

The installer never overwrites your user configuration or denylist. If you edit the source, rerun `./install.sh` before building: Docker build resources are copied during installation.

## 3. Build the images (explicit; one target at a time)

```bash
sandbox build preparer    # Git + git-filter-repo + preflight checker
sandbox build base        # Ubuntu 24.04 + Python, Git and common tools
sandbox build opencode    # FROM sandbox/base + Node + OpenCode 2

# Optional Intel GPU environment:
sandbox build python-xpu     # FROM Intel's published XPU PyTorch image
sandbox build opencode-xpu   # FROM sandbox/python-xpu + Node + OpenCode 2
```

You only need `preparer` plus images for the applications you will actually run. If you only need OpenCode + XPU, build `preparer`, `python-xpu`, and `opencode-xpu`.

**Build graph:**

```text
python:3.12-slim-bookworm -> sandbox/preparer:v1
ubuntu:24.04             -> sandbox/base:v1 -> sandbox/opencode:v1
intel/pytorch:xpu-2.14.0-ubuntu24.04 -> sandbox/python-xpu:v1 -> sandbox/opencode-xpu:v1
```

Intel XPU does **not** have to inherit the small CPU `base` image. Intel's own tested GPU stack is its base, which is more important for GPU reliability. Both OpenCode images share the **same reusable Dockerfile**; they do not force PyTorch to be rebuilt when OpenCode changes. Images have locally versioned tags; change the tags in config when you intentionally upgrade a runtime or OpenCode version.

BuildKit uses regular Docker layer caching plus persistent cache mounts for `apt` and `npm`. The upstream Intel PyTorch image is pulled as a prebuilt layer instead of compiling or installing a second PyTorch stack. For a controlled refresh:

```bash
sandbox build opencode --pull
# Or rebuild from scratch if you explicitly intend to:
sandbox build python-xpu --no-cache --pull
```

`--pull` refreshes external base images, and `--no-cache` intentionally discards build-step reuse. Neither flag is used by default. Docker builds use **only the installed project resources** as context; your Git repositories are runtime bind mounts, **never** a `COPY . .` during the image build. Build cache can still be lost if you explicitly run Docker prune commands.

**Image ID pinning:** When a sandbox first uses an image profile, its exact image ID (`sha256:...`) is saved in its metadata. Rebuilding the same tag later doesn't silently change an existing sandbox's runtime. A named volume for virtualenvs is keyed by the **pinned Python base runtime image ID**, allowing shell and OpenCode to share environments without accidentally crossing Python upgrades. To use a newer image for an existing session, create another named sandbox; there is deliberately no silent migration.

## 4. Configure

Edit `~/.config/sandbox/config.toml`:

```toml
[sandbox]
memory = "8g"
pids_limit = 512
network = "bridge"              # unrestricted outbound network
# network = "none"              # offline

gpu_devices = ["/dev/dri/renderD128"]  # optional (choose your actual device)
forward_env = []                # no secrets forwarded by default
mount_global_skills = true
opencode_config = "apps/opencode.jsonc"
venv_volume = true

[images]
preparer = "sandbox/preparer:v1"
base = "sandbox/base:v1"
python-xpu = "sandbox/python-xpu:v1"
opencode = "sandbox/opencode:v1"
opencode-xpu = "sandbox/opencode-xpu:v1"
```

The OpenCode configuration is a separate file, `~/.config/sandbox/apps/opencode.jsonc`. Global skill directories are mounted read-only when launching OpenCode, if they exist:

- `~/.config/opencode/skills` -> `/root/.config/opencode/skills`
- `~/.agents/skills` -> `/root/.agents/skills`
- `~/.claude/skills` -> `/root/.claude/skills`

Skills committed in the sandbox repository are available in the repository itself. The **entire host `~/.config/opencode` is NOT mounted**. OpenCode authentication/application data is saved to a Docker named volume per sandbox; it is accessible to processes inside that sandbox. OAuth or `/connect` can be performed inside OpenCode if supported by your provider. To supply API keys from the host explicitly, add variable *names* to `forward_env`, e.g. `ANTHROPIC_API_KEY`, and export the variables in your host shell. This knowingly exposes those values to code in the runtime.

### Private denylist

For any `--anonymize` sandbox, edit the file:

```bash
$EDITOR ~/.config/sandbox/denylist.txt
chmod 600 ~/.config/sandbox/denylist.txt
```

Example entries (literal, case-insensitive, one per line; `#` starts a comment):

```text
Jane Example
jane@example.com
private-github-username
personal-domain.example
old-employer
```

**At least one active entry is required** for anonymized sandboxes. The template contains comments only, so you must configure it first. You can also pass `sandbox create ... --anonymize --denylist /path/to/private-file`.

The file is mounted only into the trusted preparer; it is **never** provided to OpenCode. Before launch, the preparer checks all decoded Git objects (including unreachable objects), commit identities, current workspace files, Git metadata, paths, symlink targets, and read-only skill/config mounts. It aborts on a match. Source commits are rewritten to `Sandbox <sandbox@invalid>`; Git remotes are removed. A clean second clone discards pre-rewrite unreachable objects from the intermediate object store.

**Limits:** a denylist does not discover unknown names, encoded data, compressed archives, image metadata, secrets not in the list, or data outside its scanned mounts. Historical signatures and hashes are rewritten; embedded names in commit messages and source content can remain, and are blocked only if a configured term matches. A completed scan is not a guarantee of anonymity. The runtime can modify its workspace after scanning; the checks repeat before subsequent `run`/`exec` calls for anonymized sandboxes.

## 5. Named sandbox workflow

Use a **clean and committed** source repository (including no untracked files). This prevents accidentally omitting local changes from the independent clone.

```bash
sandbox create dev ~/projects/my-project
sandbox create ai-experiment ~/projects/my-project --anonymize
sandbox list
sandbox inspect ai-experiment
```

The anonymization choice is fixed at creation. A plain sandbox retains original historical commit identities but still has **no remotes or host credentials** and uses a synthetic identity for new commits by default. It is not an anonymized environment.

Run the shell or OpenCode:

```bash
sandbox run dev --app shell
sandbox run ai-experiment --app opencode
sandbox run ai-experiment --app opencode --runtime python-xpu
sandbox exec ai-experiment --runtime python-xpu -- python -c 'import torch; print(torch.__version__)'
```

Each run starts a new **ephemeral application container** attached to the same persistent Git worktree. Branches, commits, files, and application-state volumes persist between runs. Containers are rootless, drop all Linux capabilities, use `no-new-privileges`, and are limited by memory/PID settings. Avoid launching simultaneous processes that modify the **same named sandbox**; its Git worktree and application volumes are shared and the CLI does not yet coordinate concurrent writes. They do not mount the Docker socket, original repository, commit map, denylist, SSH agent, or host Git configuration.

The agent is free to create **many commits, merges, or branches**. No particular Git workflow is imposed on it. The original Git worktree isn't touched during runs.

### Import the resulting Git graph

```bash
sandbox import ai-experiment
# In original repo:
git branch --list 'sandbox/ai-experiment/*'
git log --graph --oneline --all
```

The importer runs on the **host**, locally fetches sandbox Git objects into a temporary Git ref namespace, and reconstructs new commits using your host Git author/committer identities while translating historical parent hashes back through a private `commit-map`. This preserves new multi-parent merge commits and branch tips. It creates only review branches under `refs/heads/sandbox/<name>/...`, never checks out, merges into your existing branches, or pushes.

If the review branches already exist, it refuses to overwrite them unless you use `sandbox import <name> --force`. `--force` means replacing **review branch refs only**; your actual branches still aren't modified. Original historical commit identities stay intact on the host. New sandbox-authored commits get your host identity during import, with their timestamps retained. The file trees and new commit messages are preserved.

The importer does **not** automatically import a dirty working tree. Commit or stash changes in the sandbox first; only committed branch tips are imported. Existing source commits must remain available in the original repository; if you rewrite/prune the original history after creating the sandbox, importing might fail.

### Remove

```bash
sandbox destroy ai-experiment --yes
```

This removes the sandbox checkout, private mapping, metadata, and its recorded Docker volumes. It does not delete original repository content or already imported review branches. It refuses to destroy a sandbox with running containers.

## 6. Intel GPU and reusable Python environments

The **host** needs functioning Intel GPU drivers, but the container needs its own matching user-space Level Zero stack. `sandbox/python-xpu` inherits Intel's published Ubuntu 24.04 XPU PyTorch image, which supplies that stack and puts its PyTorch environment at **`/opt/venv`** (not `/usr/bin/python`). Unlike the Arch host Python, that image can be reused across many containers through Docker's read-only image layers.

Find your render node:

```bash
ls -l /dev/dri/renderD*
stat -c '%n %g %G' /dev/dri/renderD*
```

Then set `gpu_devices` in `config.toml`. For a smoke test:

```bash
sandbox exec dev --runtime python-xpu -- python -c 'import torch; print(torch.xpu.is_available())'
```

Expected: `True`, for supported/compatible GPUs. Rootless Docker device permissions can be subtle: `--device` and a supplemental group ID are provided, but Docker user-namespace mapping and ACLs may still prevent access. Fix the host user/device authorization and rootless mappings rather than resorting to `--privileged` or world-writable `/dev/dri` nodes. The memory/pid limits also depend on rootless cgroup delegation.

**Create your own environments that reuse the existing XPU PyTorch packages:**

```bash
sandbox run dev --app shell --runtime python-xpu
sandbox-venv experiment1
source /opt/venvs/experiment1/bin/activate
python -c 'import torch; print(torch.__file__)'
python -m pip install transformers  # goes into experiment1
```

Why `sandbox-venv` instead of `python -m venv --system-site-packages`? Intel's base PyTorch is installed into the **preexisting `/opt/venv` virtual environment**. A new venv with `--system-site-packages` normally shares the *real system* Python site packages, not a parent virtualenv's packages. `sandbox-venv` creates a new venv and adds a `.pth` reference to `/opt/venv`'s site-packages. It shares large read-only packages, while new packages go into the new venv. Package shadowing remains possible: installing another `torch` into the child venv will override the shared one. It does **not** share the host's Arch `/usr/bin/python` packages directly.

The `/opt/venvs` Docker volume is per sandbox and per **Python base image ID**, shared across shell and OpenCode profiles built on that base. Do not use the same venv across interpreter upgrades. The XPU base layer and its preinstalled PyTorch are shared by Docker containers, not duplicated per sandbox.

## 7. What it deliberately does NOT guarantee

- **Anonymization is opt-in** and literal-denylist-based, not complete data-loss prevention.
- **No SSH key is mounted**, and Git remotes are removed. With unrestricted network access, an agent can still send files to arbitrary endpoints or configure new remote URLs. Git-command deny rules would not prevent this.
- **The agent can read its own LLM credentials**, application state and writable repository. Credentials and network requests are inside your chosen trust boundary.
- Rootless Docker reduces host privilege risk but is **not** a VM or a defense against every kernel exploit.
- The preparation container is trusted, has a temporary **read-only source Git mount**, and no network. Only the application container is untrusted. Do not pass an untrusted preparer image.
- Docker images are pinned *per sandbox at first use*, not content-verified against cryptographic manifests. For maximum build supply-chain reproducibility, pin external `FROM` images by digest, use lockfiles and audit upstream packages.
- All generated local Docker volumes and checkouts can contain code, models, tokens, or secrets; they are not encryption boundaries.
- Git LFS content and submodules are not automatically expanded into the clone. Checkouts based on submodules, LFS, sparse, or partial/shallow history may need manual support or fail.

## 8. Test and troubleshoot

Run the local Git/unit tests without Docker:

```bash
python -m unittest discover -s tests -v
```

Check build caching explicitly:

```bash
sandbox build base
sandbox build base            # Observe 'CACHED' in BuildKit logs
sandbox build opencode
sandbox build opencode        # OpenCode layer cached
```

If `sandbox create` fails, ensure the preparer image is built, Docker is rootless, the source Git checkout is clean, and for `--anonymize` that the private denylist contains at least one real term. Check errors printed by the preparation worker: on failure the incomplete sandbox is removed and the agent never runs.

If `sandbox run` fails due to missing images, build the requested runtime profile explicitly. `sandbox build opencode-xpu` intentionally refuses to run until `sandbox build python-xpu` has succeeded. If you change your Docker tag settings, old sessions continue using the pinned image IDs until you intentionally make a new named sandbox.

For a simpler first test, create a small throwaway repo and run `sandbox create testing ./repo --anonymize`, followed by `sandbox run testing --app shell`, before providing any valuable repositories.

## Project organization

```text
bin/sandbox                       # Public Python CLI
libexec/sandboxlib/cli.py         # Lifecycle and runtime orchestration
libexec/sandboxlib/prepworker.py  # Trusted Git preparation
libexec/sandboxlib/preflight.py   # Content / history denylist screening
libexec/sandboxlib/importer.py    # Commit-map translation and host review refs
images/preparer/Dockerfile       # Small, network-disabled preparation runtime
images/base/Dockerfile           # CPU runtime
images/python-xpu/Dockerfile     # Intel GPU/PyTorch runtime
images/python-xpu/sandbox-venv   # Venv helper for Intel's /opt/venv
images/opencode/Dockerfile       # App layer (base or python-xpu)
examples/                       # Private config and denylist templates
install.sh
```

## Upstream references

- [ArchWiki: Docker / rootless daemon](https://wiki.archlinux.org/title/Docker)
- [Docker: Rootless mode](https://docs.docker.com/engine/security/rootless/)
- [Docker: Dockerfile cache mounts](https://docs.docker.com/reference/dockerfile/#run---mounttypecache)
- [OpenCode 2 documentation](https://opencode.ai/v2/docs)
- [Intel PyTorch container repository](https://hub.docker.com/r/intel/pytorch)
- [PyTorch XPU guide](https://docs.pytorch.org/docs/stable/notes/get_start_xpu.html)
