# Bring your own runtime

`sandbox` does not need to know which applications you run. A runtime is a **named Docker image selection plus a small amount of execution configuration**. The two supplied runtime profiles, `base` and `python-xpu`, are examples.

## 1. Use an existing image

Add a runtime to `~/.config/sandbox/config.toml`:

```toml
[runtimes.node]
image = "node:22-bookworm"
shell = "/bin/bash"
venv_volume = false
devices = []
```

Ensure the image is available through your rootless Docker daemon (e.g. `docker pull node:22-bookworm`). Then:

```bash
sandbox runtimes
sandbox run dev --runtime node
sandbox exec dev --runtime node -- node --version
```

The image is **pinned by immutable image ID** in the named sandbox's metadata on first use. Replacing the tag in the Docker store doesn't silently change existing sandboxes. To adopt a changed image, create another named sandbox (or intentionally implement a migration); Python venvs should not be carried across incompatible images.

## 2. Build a custom image

You may define a Dockerfile and its build context. For example, create `~/.config/sandbox/runtimes/node/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates bash ripgrep \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
CMD ["/bin/bash"]
```

And configure:

```toml
[runtimes.node]
image = "local/sandbox-node:22"
shell = "/bin/bash"
venv_volume = false
dockerfile = "~/.config/sandbox/runtimes/node/Dockerfile"
context = "~/.config/sandbox/runtimes/node"
```

```bash
sandbox build node
sandbox run dev --runtime node
```

You may also build externally and specify only the resulting image tag. Dockerfile/context settings are **optional**. Relative `dockerfile` and `context` paths are resolved from the user's sandbox config directory. For custom runtime builds, an omitted `context` defaults to the Dockerfile's parent directory. The example `base` and `python-xpu` profiles use explicit `bundled:` build paths in the example configuration. `sandbox build preparer` builds the separate Git preparation image.

**Never set your personal home directory or your private Git repository as a build context.** A Dockerfile can accidentally copy sensitive context files into its layers. Keep build contexts small with a `.dockerignore`.

## 3. Runtime requirements

A runtime image should:

- Be compatible with the host CPU architecture and Linux kernel.
- Provide `/bin/sh` (required for tool PATH initialization when shared tools are enabled).
- Provide an interactive shell at the configured `shell` path (defaults to `/bin/bash`). For Alpine, configure `shell = "/bin/sh"`.
- Allow normal command execution when the CLI supplies a command. Avoid custom entrypoints that ignore/transform commands; if necessary, create a wrapper image with an appropriate `ENTRYPOINT`.
- Work with `/workspace` as the working directory and `HOME=/root`, since the current launcher deliberately runs UID 0 *inside a rootless Docker user namespace*. This isn't a generic arbitrary-UID container mechanism yet.
- Include Git and CA certificates if you need those tools; Python, Node and GPUs are **not mandatory**.

The manager mounts the same independent repository at `/workspace` for every runtime. Per-sandbox app data/config/cache volumes are mounted at `/root/.local/share`, `/root/.config`, `/root/.cache`. Shared host tools are mounted read-only at `/opt/tools`. When enabled, `/opt/venvs` receives a persistent named volume unique to the sandbox and pinned image ID.

## 4. Optional devices and virtual environments

Device access is **per runtime**, not tied to `python-xpu`. For example:

```toml
[runtimes.intel-compute]
image = "local/my-intel-runtime:v1"
shell = "/bin/bash"
venv_volume = true
devices = ["/dev/dri/renderD128"]
```

The launcher exposes only those device nodes via Docker `--device` and adds their numeric host GIDs. This may require extra rootless Docker device/ACL configuration, which varies by host; a GID without a name in `/etc/group` is not necessarily a failure. Device access is **sensitive**: do not expose more host hardware than necessary.

For an ordinary non-Python runtime, use `venv_volume = false` to avoid unnecessary `/opt/venvs` volume creation. A generic runtime doesn't need PyTorch. The `sandbox-py` helper is optional; its special inheritance mode is intended for images containing a base Python/PyTorch environment.

## 5. Caching and disk usage

`docker build` runs with BuildKit and `--progress=plain` by default. Builds are always explicit. BuildKit can reuse unchanged image layers and support cache mounts in your Dockerfile. Choose versioned/digest-pinned base images, place rarely changed package installation steps before source changes, use cache mounts for expensive downloads, and keep the build context small. The Git checkout is **never** copied into the packaged runtime image recipes.

```bash
sandbox build node                     # normal cached build
sandbox build node --pull              # check for updated upstream base
sandbox build node --no-cache          # intentionally bypass layer reuse
docker system df -v
docker buildx du
```

When adding a custom image, avoid building project source into it: the source is mounted when the sandbox runs. A custom image may nevertheless use `COPY` from its own *small toolchain context*.

References: [Docker BuildKit](https://docs.docker.com/build/buildkit/), [Docker build cache optimization](https://docs.docker.com/build/cache/optimize/).

## 6. No implicit built-in runtime logic

The example profiles `base` and `python-xpu` are normal entries in `config.toml`, exactly like third-party profiles. You can rename, remove, or replace them. `sandbox build NAME` uses the `dockerfile` and `context` declared in each profile; `sandbox build preparer` remains a separate reserved core command. See [Configuration](configuration.md) and [Migration](migration.md).
