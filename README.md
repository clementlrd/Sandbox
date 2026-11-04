# sandbox

`sandbox` is a small CLI for working on Git repositories inside **rootless Docker containers**. It creates named, independent copies of repositories, keeps workspace and application state between sessions, and lets you import selected work back as review branches. Historical Git identities can optionally be anonymized before a container sees the repository.

Choose a Docker runtime for each command, enter a shell, and use the tools you need. Images are built only on request; the checkout is mounted rather than baked into an image. This is useful for experiments, automated coding agents, and everyday development that you want to keep separate from the host.

> **Security status:** This project has **not undergone an independent security audit**. Rootless Docker reduces host exposure but is not a VM-grade boundary. Mounted files, credentials, network access, setup recipes, and GPU devices all affect your risk. See [Security and limitations](docs/security.md).

## Quick start

Requirements: Linux, Python 3.11+, Git, Docker CLI + BuildKit, and a **rootless Docker daemon**. See [installation and prerequisites](docs/quickstart.md) and [Docker's rootless setup guide](https://docs.docker.com/engine/security/rootless/).

```bash
./install.sh --no-tools
# Optional shorthand: add `alias sbx=sandbox` to ~/.bashrc or ~/.zshrc.
alias sbx=sandbox

sandbox build preparer
sandbox build base

sandbox create experiment ~/projects/my-repo
sandbox run experiment
sandbox exec experiment -- git status
sandbox import experiment
sandbox destroy experiment --yes
```

For privacy-sensitive work, configure a private denylist first:

```bash
$EDITOR ~/.config/sandbox/denylist.txt
chmod 600 ~/.config/sandbox/denylist.txt
sandbox create ai-work ~/projects/my-repo --anonymize
sandbox run ai-work
```

If an intentional reference (for example your copyright name in `LICENSE`)
blocks the denylist scan, you can opt out **explicitly** when creating the
sandbox:

```bash
sandbox create ai-work ~/projects/my-repo --anonymize --skip-denylist
sandbox run ai-work
```

Initial anonymization and its checks happen during `sandbox create`.
The preparer rewrites historical Git authors/committers, checks the prepared
repository for remotes and object alternates, and (unless skipped) scans its
content against your denylist. `--skip-denylist` only skips that content scan;
it does not disable history rewriting or the preparation checks. No identity
or denylist checks run during `sandbox run` or `sandbox exec`. Changes you make
inside a sandbox afterward are your responsibility.

`refresh` prepares and screens new incoming host history separately; it does not
rescan existing sandbox work. `--skip-denylist` applies only to the explicit
`create` or `refresh` invocation, never to `run` or `exec`.
The import operation uses the private commit mapping to
restore original ancestry before creating review branches with your host Git
identity.

The source repository must be clean at creation. `sandbox import` creates *review branches* in the original repository—it never merges or pushes. Commit changes inside the sandbox before importing. `sandbox destroy` removes the sandbox workspace and its named volumes, including application authentication and virtual environments.

## Refresh a long-lived sandbox

Bring committed host changes into an existing sandbox without replacing its
checkout, worktrees, application data, or environments:

```bash
# On the host, after committing changes to main:
sandbox refresh experiment --branch main

# Inside the sandbox, in the branch/worktree you want to update:
git merge host/main
# Or: git switch -c integration host/main
```

Refresh updates only `refs/remotes/host/main`; it never merges, rebases, or changes
working files. Dirty host files are ignored. Select each host branch explicitly;
no live host remote or credentials are configured in the sandbox. Host history
rewrites are allowed and reported as non-fast-forward updates.

Import and refresh extend the host-only commit mapping in both directions, so
accepted sandbox work returns as the same sandbox commits, and refreshed teammate
commits import back as their exact originals. Sandbox local branches (including
`main`) still import as `sandbox/NAME/BRANCH`; `host/main` is not exported as a
branch, though its ancestors are included when merged into a local branch.

Anonymized refresh uses the current private denylist by default, even if creation
skipped it. Use `--denylist PATH` or explicitly `--skip-denylist` for that refresh.
Neither option changes the policy of later invocations.

After upgrading, reinstall the CLI and run `sandbox build preparer` explicitly;
existing sandboxes and runtime images do not need rebuilding. See
[refresh and upgrade details](docs/quickstart.md#refresh-from-the-host).

## Runtime images

A runtime is a Docker image plus a few options (shell, devices, GPU access, and optional persistent Python environments). The project includes **example image recipes**, all opt-in:

| Runtime | What it provides | Extra host requirements |
| --- | --- | --- |
| `base` | General Linux development tools including `git`, `jq`, and `tmux` | None beyond Docker |
| `python-xpu` | Python + PyTorch for Intel XPU acceleration | Compatible Intel GPU; render-device access |
| `python-cuda` | Python + PyTorch with NVIDIA CUDA support | NVIDIA driver; NVIDIA Container Toolkit for rootless Docker |

For example, to build and use NVIDIA CUDA (it is **not downloaded or built automatically**):

```bash
sandbox build python-cuda
sandbox run experiment --runtime python-cuda
sandbox exec experiment --runtime python-cuda -- \
  python -c 'import torch; print(torch.cuda.is_available())'
```

GPU setup is host-specific; see [NVIDIA CUDA setup and troubleshooting](docs/runtimes.md#nvidia-cuda-and-rootless-docker). These are examples, not required components of the CLI.

You can instead use **any compatible local image**:

```toml
# ~/.config/sandbox/config.toml
[runtimes.node]
image = "node:22-bookworm"
shell = "/bin/bash"
venv_volume = false
```

```bash
docker pull node:22-bookworm
sandbox runtimes
sandbox run experiment --runtime node
sandbox exec experiment --runtime node -- node --version
```

You can also specify a Dockerfile and build context for `sandbox build NAME`. Profiles are ordinary configuration; the CLI does not special-case a language, GPU brand, or application. See [Runtime guide](docs/runtimes.md).

## Tools and persistent data

`${XDG_DATA_HOME:-~/.local/share}/sandbox/tools/bin` is available inside containers as `/opt/tools/bin` via a **read-only** host mount. You can install portable tools there, such as the optional OpenCode CLI:

```bash
./install.sh --tools opencode    # optional, host-managed tool
# ./install.sh --no-tools       # skips downloads
```

The example runtime images include `jq` and `tmux` as OS packages. You can use these or any other programs directly from the shell; there is no application-profile machinery. Note that a `tmux` server stops when its ephemeral container exits.

`/workspace` holds the persistent independent Git repository. Application data, configuration, and cache have per-sandbox Docker volumes. `/opt/venvs` is optional and isolated by **sandbox and runtime image ID**, so environments made under `base`, `python-xpu`, and `python-cuda` do not get accidentally mixed. `sandbox-py` creates project venvs and installs dependencies from `pyproject.toml` using pip, reusing a base image's PyTorch installation where provided. The helper is mounted through the shared tools directory, not copied into runtime images. It does not consume `uv.lock`.

Inside a Python-capable runtime with `venv_volume = true`:

```bash
sandbox-py init myenv                  # install from pyproject.toml
sandbox-py init myenv --empty          # no project metadata needed
sandbox-py install myenv requests
sandbox-py install myenv -r requirements.txt
/opt/venvs/myenv/bin/python script.py
```

Without `pyproject.toml`, interactive `init` asks whether to create an empty environment; noninteractive use requires `--empty`. Rerun `init` after changing `pyproject.toml`, or `install -r` after changing a requirements file. Both reuse the environment and install versions needed to satisfy the current requirements; request newer versions explicitly (for example, `"requests>=2.32"`). Removed dependencies are not automatically uninstalled. Requirements files can also contain `-e .` to install the project itself.

When runtime PyTorch is inherited, installs are constrained to its version and checked with a pip dry run before installation. Any plan to install another copy of PyTorch—even the same version—is rejected. Manual pip commands bypass these protections.

## Trusted setup recipes

Use explicit host-side scripts for tasks such as copying browser-authenticated OpenCode credentials into a sandbox:

```bash
sandbox setup list
# First authenticate on the host (browser-based login works there):
~/.local/share/sandbox/tools/bin/opencode auth login anthropic

# Explicit provider; no default. Credentials are piped, not copied from host files.
SANDBOX_PROVIDER=anthropic sandbox setup experiment opencode-provider
sandbox run experiment

# A second provider or refreshed login in the same sandbox:
SANDBOX_PROVIDER=openai sandbox setup experiment opencode-provider --force
```

Recipes live in `~/.config/sandbox/recipes/`, execute with **host permissions**, and must be reviewed before use. The OpenCode recipe selects the provider with the required `SANDBOX_PROVIDER` environment variable. Recipe completion is tracked per sandbox and recipe name; use `--force` when changing providers or refreshing tokens. **Claude Code** is a separate application, not an OpenCode provider: its login credentials cannot be transferred with `opencode auth export`. Claude Code supports pasting a browser authorization code into its terminal when a container callback is unavailable. See [Configuration](docs/configuration.md) and [Security](docs/security.md).

## Documentation

- [Installation and lifecycle](docs/quickstart.md)
- [Custom runtimes, CUDA/XPU, and build caching](docs/runtimes.md)
- [Configuration reference](docs/configuration.md)
- [Architecture and persistence](docs/architecture.md)
- [Security and privacy limitations](docs/security.md)
- [Uninstall and data cleanup](docs/uninstall.md)
- [Development and testing](docs/development.md)
- [GitHub Actions CI explained](docs/ci.md)

## About this project

This project was developed with substantial AI assistance, with human direction, testing, and integration.

This is a personal utility shared as-is for others who may find it useful. No support or ongoing maintenance is promised. It has not undergone an independent security audit; see [Security and privacy limitations](docs/security.md).
