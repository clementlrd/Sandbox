# sandbox

**Named, isolated Git workspaces powered by rootless Docker.**

`sandbox` prepares independent Git repositories, optionally anonymizes historical commit identities, starts ephemeral containers with persistent workspace/application state, and imports commits back as review branches. It is **not tied to OpenCode, Intel GPUs, or Python**. Bring any compatible Linux runtime image, and run whatever command you need inside it.

> **Project status: experimental.** This is a useful personal-development tool, **not an audited security boundary**. Rootless Docker helps reduce host exposure, but allowed mounts, runtime images, setup recipes, credentials, network access, and Git content are part of the trust model. Read [Security and limitations](docs/security.md) before use.

## Quick start

**Upgrading from an earlier sandbox archive?** First migrate your existing `config.toml` using the [one-time migration guide](docs/migration.md). This release intentionally rejects older keys instead of translating them automatically. Existing named sandboxes and their volumes can normally remain untouched.

Requirements: Linux, Python 3.11+, Git, Docker CLI + BuildKit, and a working **rootless Docker daemon**. Set up rootless Docker according to [the official guide](https://docs.docker.com/engine/security/rootless/).

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

For privacy-sensitive agent work, configure a private denylist, then create with `--anonymize`:

```bash
$EDITOR ~/.config/sandbox/denylist.txt
chmod 600 ~/.config/sandbox/denylist.txt
sandbox create ai-work ~/projects/my-repo --anonymize
sandbox run ai-work
```

The source checkout must be clean at creation time. `sandbox import` creates review branches in the original repository; it does **not** merge, check out, or push them. Commit changes inside the sandbox before importing. `destroy` deletes the sandbox workspace and its named volumes, including credentials and virtual environments.

## Bring your own runtime

You can use an existing, locally available Docker image:

```toml
# ~/.config/sandbox/config.toml
[runtimes.node]
image = "node:22-bookworm"
shell = "/bin/bash"
venv_volume = false
```

```bash
sandbox runtimes
sandbox run experiment --runtime node
sandbox exec experiment --runtime node -- node --version
```

Or configure your own Dockerfile with `dockerfile` and `context`, then run `sandbox build node`. **Images are built only when explicitly requested** and are pinned by image ID on first use per sandbox/runtime name. The repository is never copied into a Docker image during a build.

See [Adding a runtime](docs/runtimes.md) for the complete contract, build caching, device passthrough and examples. `python-xpu` is an optional example runtime, not a requirement.

## Tools and persistence

The optional host tools directory `${XDG_DATA_HOME:-~/.local/share}/sandbox/tools` is mounted read-only at `/opt/tools`. Put compatible binaries under `tools/bin`; the launcher prepends `/opt/tools/bin` to the runtime's existing `PATH`. Applications do not need profiles. For optional installers:

```bash
./install.sh --tools opencode,jq  # downloads external executables onto the host
# Or use ./install.sh --no-tools to avoid downloads
```

Application data/config/cache volumes persist **per sandbox**. `/opt/venvs` volumes persist **per sandbox and exact runtime image ID**, when enabled. Runtime system package changes made during a session disappear with the container. The Git workspace is a writable host-side bind mount and persists independently of the container.

For Python projects, `sandbox-py` is available in the shared tools directory and can install `pyproject.toml` dependencies with pip into `/opt/venvs/NAME`, constraining inherited XPU PyTorch where available. It does not reproduce `uv.lock`.

## Setup recipes

Use explicit, **trusted host-side** setup scripts for one-time tasks like transferring browser-authenticated OpenCode credentials:

```bash
sandbox setup list
sandbox setup experiment opencode-openai --runtime base
sandbox run experiment --setup opencode-openai
```

Recipes live in `~/.config/sandbox/recipes` and can access host resources. Only run scripts you trust. See [Configuration](docs/configuration.md) and [Security](docs/security.md).

## Documentation

- [Quick start and lifecycle](docs/quickstart.md)
- [Add your own Docker runtime](docs/runtimes.md)
- [Configuration reference](docs/configuration.md)
- [One-time migration from earlier configurations](docs/migration.md)
- [Safe uninstall and optional complete cleanup](docs/uninstall.md)
- [Architecture and storage model](docs/architecture.md)
- [Security and privacy limitations](docs/security.md)
- [Development and testing](docs/development.md)
- [Readiness checklist for a public release](docs/release-readiness.md)

## Contributing and licensing

See [CONTRIBUTING.md](CONTRIBUTING.md). A final open-source license has **not yet been selected**; choose and add a `LICENSE` file before presenting the repository as open source. Until then, publication of source code alone does not grant general reuse rights.
