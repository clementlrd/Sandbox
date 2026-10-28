# sandbox

`sandbox` is a small CLI for working on Git repositories inside **rootless Docker containers**. It creates named, independent copies of repositories, keeps workspace and application state between sessions, and lets you import selected work back as review branches. Historical Git identities can optionally be anonymized before a container sees the repository.

Choose a Docker runtime for each command, enter a shell, and use the tools you need. Images are built only on request; the checkout is mounted rather than baked into an image. This is useful for experiments, automated coding agents, and everyday development that you want to keep separate from the host.

> **Security status:** This project has **not undergone an independent security audit**. Rootless Docker reduces host exposure but is not a VM-grade boundary. Mounted files, credentials, network access, setup recipes, and GPU devices all affect your risk. See [Security and limitations](docs/security.md).

## Quick start

Requirements: Linux, Python 3.11+, Git, Docker CLI + BuildKit, and a **rootless Docker daemon**. See [installation and prerequisites](docs/quickstart.md) and [Docker's rootless setup guide](https://docs.docker.com/engine/security/rootless/).

```bash
./install.sh --no-tools
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

The source repository must be clean at creation. `sandbox import` creates *review branches* in the original repository—it never merges or pushes. Commit changes inside the sandbox before importing. `sandbox destroy` removes the sandbox workspace and its named volumes, including application authentication and virtual environments.

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

`/workspace` holds the persistent independent Git repository. Application data, configuration, and cache have per-sandbox Docker volumes. `/opt/venvs` is optional and isolated by **sandbox and runtime image ID**, so environments made under `base`, `python-xpu`, and `python-cuda` do not get accidentally mixed. `sandbox-py` can install dependencies from `pyproject.toml` using pip, reusing a base image's PyTorch installation where provided. It does not consume `uv.lock`.

## Trusted setup recipes

Use explicit host-side scripts for tasks such as copying browser-authenticated OpenCode credentials into a sandbox:

```bash
sandbox setup list
sandbox setup experiment opencode-openai
sandbox run experiment --setup opencode-openai
```

Recipes live in `~/.config/sandbox/recipes/`, execute with **host permissions**, and must be reviewed before use. See [Configuration](docs/configuration.md) and [Security](docs/security.md).

## Documentation

- [Installation and lifecycle](docs/quickstart.md)
- [Custom runtimes, CUDA/XPU, and build caching](docs/runtimes.md)
- [Configuration reference](docs/configuration.md)
- [Architecture and persistence](docs/architecture.md)
- [Security and privacy limitations](docs/security.md)
- [Uninstall and data cleanup](docs/uninstall.md)
- [Development and testing](docs/development.md)
- [Release-readiness checklist](docs/release-readiness.md)

## Development

This project was developed with substantial AI assistance, with human direction, testing, and integration.

It is provided as-is. Although designed with isolation and security in mind, **it has not undergone an independent security audit**. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

A final open-source license has not yet been added. Choose one before publishing this repository as openly licensed software.
