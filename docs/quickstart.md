# Getting started

This guide uses a rootless Docker daemon on Linux. The CLI is currently a Linux/local-host tool, not a macOS/Windows or remote-Docker workflow.

## Prerequisites

- Linux host with Python 3.11+, Git, Docker and `docker buildx`/BuildKit.
- A **rootless** Docker daemon, configured and selected as the current Docker context. Rootful mode is intentionally rejected.
- `curl`, `tar`, `sha256sum` if you choose to download the optional OpenCode executable.
- Enough disk space for your chosen runtime; large GPU/PyTorch images can require many GB.

On Arch, typical packages are `python`, `git`, `docker`, `docker-buildx`, `curl`, `tar`, and `coreutils`. Rootless setup additionally requires appropriate subordinate UID/GID ranges and your distribution's rootless Docker components. Follow [Docker's rootless documentation](https://docs.docker.com/engine/security/rootless/) rather than blindly enabling a rootful daemon.

Verify:

```bash
docker info --format '{{json .SecurityOptions}}'  # includes "rootless"
```

## Install

From the repository root:

```bash
./install.sh --no-tools       # does not download OpenCode
# or: ./install.sh           # interactive tool selection
```

The installer replaces its managed code/build resources. It installs the `sandbox` command under `~/.local/bin`, Python helpers under `~/.local/libexec/sandbox`, examples/config under `$XDG_CONFIG_HOME/sandbox` (default `~/.config/sandbox`) and Docker build resources/tools under `$XDG_DATA_HOME/sandbox` (default `~/.local/share/sandbox`). Existing config, denylist and recipes are preserved.

Ensure `~/.local/bin` is in `PATH`. The installer does not need sudo.

For shorter interactive commands, optionally add this line to your
`~/.bashrc` or `~/.zshrc`, then reload the shell:

```bash
alias sbx=sandbox
```

For example, `sbx create dev ~/projects/my-repo` and `sbx run dev` are
identical to their `sandbox` equivalents. Shell aliases only affect shells
where the alias is defined; scripts should still call `sandbox` directly.

To uninstall later, see [Uninstall](uninstall.md).

## Prepare images explicitly

```bash
sandbox build preparer
sandbox build base
# Optional accelerator profiles: build only those you actually need.
sandbox build python-xpu      # Intel GPU
sandbox build python-cuda     # NVIDIA GPU, requires host NVIDIA toolkit
```

`create`, `run`, `exec`, `setup` and `import` never build images. The `base`, `python-xpu`, and `python-cuda` images include `jq` and `tmux`. After building, verify with `sandbox exec dev -- jq --version` and `sandbox exec dev -- tmux -V`. `tmux` servers do not survive the removal of an ephemeral container. Their profiles are declared explicitly in `config.toml`; delete or replace them as you prefer. NVIDIA CUDA users must configure the host toolkit first (see [CUDA setup](runtimes.md#nvidia-cuda-and-rootless-docker)). See [Runtimes](runtimes.md) for third-party images and caching.

## Create a named sandbox

```bash
sandbox create dev /absolute/path/to/repo
sandbox list
sandbox inspect dev
```

The source Git repository must be clean (no untracked/uncommitted changes) and have at least one commit. A prepared *independent* clone is stored under `$XDG_STATE_HOME/sandbox/instances/NAME/repo` (default `~/.local/state/sandbox/instances/NAME/repo`). No source remote is provided to the application container.

For anonymization, add one private denylist term per line to `~/.config/sandbox/denylist.txt` (mode `0600`) and create with `--anonymize`. The preparer rewrites commit identities in a network-disabled container, saves the original/anonymous commit mapping *outside* the agent mount, and checks denylist terms before launching the application. This is **best-effort**, not total anonymization.

If a known intentional identifier (such as your name in `LICENSE`) causes
a false-positive for your workflow, you can explicitly opt out:

```bash
sandbox create agent /absolute/path/to/repo --anonymize --skip-denylist
sandbox run agent
# For already-created anonymized environments, bypass only this execution:
sandbox run existing-agent --skip-denylist
```

`--skip-denylist` on `create` is stored in the sandbox metadata and remains
active on subsequent launches. The same flag on `run` or `exec` is temporary.
The Git identity rewrite and structural Git checks remain active, but **no
content screening takes place**; source files or historical commit messages can
still reveal identifying information. This is not a privacy guarantee.

## Run commands

```bash
sandbox run dev                           # interactive /bin/bash by default
sandbox exec dev -- git status            # one-off ephemeral container
sandbox exec dev --runtime base -- python --version
```

Use `--runtime NAME` to select another configured image. `sandbox run` starts an ephemeral container but the workspace and persistent volumes survive exit. No `--app` option is needed: run OpenCode, a shell, compiler or test command yourself.

## Import, then destroy

Within the sandbox, commit desired changes. On the host:

```bash
sandbox import dev
# Inspect refs/heads/sandbox/dev/* in your original repository.
# Review diffs and merge/cherry-pick deliberately.
sandbox destroy dev --yes
```

Import recreates new agent commits using the host's configured author/committer and maps historical parents back where possible. It supports multiple branches and merges in tested cases; tags, submodules, Git LFS and various unusual Git states need more validation. Import is *not* a safe backup for uncommitted changes: commit them before importing.

`destroy` permanently removes the sandbox's independent repository, recorded volumes, app data, cached app files and Python venvs. It does **not** delete the original source repository, your host tools, or Docker images/build cache. It refuses while labeled application containers are running.

## Troubleshooting

- **"Docker is not rootless"**: select/setup the rootless Docker context. Don't bypass the check for untrusted agents.
- **"image not built"**: explicitly build/configure the referenced runtime image.
- **"cannot find name for group ID ..."**: a device GID may be numeric inside the container; verify real permissions and GPU availability rather than assuming the warning indicates failure.
- **No venv after switching runtimes**: `/opt/venvs` storage is keyed to the *exact image ID*. Return to the same runtime/image or initialize a new environment.
- **Denylist match in Git history**: an old deleted file or commit message may contain the term. The preflight scans historical reachable/unreachable objects too.
- **Credentials missing after destroy**: application data volumes were removed. Import credentials using an explicit host recipe into the new sandbox.
