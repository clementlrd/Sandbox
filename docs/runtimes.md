# Bring your own runtime

`sandbox` does not need to know which applications you run. A runtime is a **named Docker image selection plus a small amount of execution configuration**. The bundled `base`, `python-xpu` (Intel) and `python-cuda` (NVIDIA) profiles are examples. Each image recipe includes common development utilities such as `jq` and `tmux`; only build what you need.

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

The image is **pinned by immutable image ID** in the named sandbox's metadata on first use. Replacing the tag in the Docker store doesn't silently change existing sandboxes. To adopt a changed image, create a new named sandbox; Python venvs should not be carried across incompatible images.

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

You may also build externally and specify only the resulting image tag. Dockerfile/context settings are **optional**. Relative `dockerfile` and `context` paths are resolved from the user's sandbox config directory. For custom runtime builds, an omitted `context` defaults to the Dockerfile's parent directory. The bundled example profiles use explicit `bundled:` build paths in the example configuration. `sandbox build preparer` builds the separate Git preparation image.

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

For an ordinary non-Python runtime, use `venv_volume = false` to avoid unnecessary `/opt/venvs` volume creation. A generic runtime doesn't need PyTorch. The `sandbox-py` helper is installed in the shared read-only tools directory. It creates persistent project venvs itself; to inherit a GPU-enabled PyTorch installation, a runtime can provide its base Python environment at `/opt/venv`. No runtime-specific `sandbox-venv` executable is required.

### NVIDIA CUDA and rootless Docker

The optional `python-cuda` profile uses NVIDIA's versioned CUDA + cuDNN image and installs a pinned, CUDA-enabled PyTorch into `/opt/venv`, just like the Intel XPU example. It uses **Docker `--gpus all`**, not the `/dev/dri` node mechanism. This requires the **NVIDIA GPU driver on the host** plus [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) configured for the *rootless* Docker daemon. The host driver must be compatible with the container CUDA version.

NVIDIA's documented rootless setup (after installing the driver and toolkit) includes:

```bash
nvidia-ctk runtime configure --runtime=docker \
  --config="$HOME/.config/docker/daemon.json"
systemctl --user restart docker
sudo nvidia-ctk config --set nvidia-container-cli.no-cgroups --in-place
```

**Inspect these commands before running them.** The last command changes system-wide NVIDIA runtime configuration and may affect other container workloads. Also verify that your rootless Docker context is active. Test the driver/toolkit *before* trying sandbox:

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 nvidia-smi
```

If that succeeds, use the explicitly configured runtime:

```toml
[runtimes.python-cuda]
image = "sandbox/python-cuda:v1"
shell = "/bin/bash"
venv_volume = true
gpus = "all"  # or "device=0" for one NVIDIA GPU
dockerfile = "bundled:images/python-cuda/Dockerfile"
context = "bundled:."
```

```bash
sandbox build python-cuda
sandbox exec dev --runtime python-cuda -- \
  python -c 'import torch; print(torch.cuda.is_available())'
```

A `True` result indicates PyTorch sees a CUDA device. Without an NVIDIA GPU, the image may build but GPU checks will return `False`. The bundled example uses CUDA **12.6** with PyTorch **2.9.1** (`cu126`); adjust and pin both together if your driver/requirements differ. PyTorch and CUDA dependencies are downloaded **once during the explicit image build** and reused by Docker layers. This example has not been end-to-end tested on NVIDIA hardware in this project.

The `gpus` runtime property is optional and defaults to disabled. It accepts `"all"`, a positive count (such as `"1"`), or a single device selection (e.g. `"device=0"`). `devices = ["/dev/dri/renderD128"]` is independent and remains appropriate for Intel/DRM devices. Don't pass raw NVIDIA device paths as a substitute for Toolkit configuration.

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

The example profiles `base`, `python-xpu`, and `python-cuda` are normal entries in `config.toml`, exactly like third-party profiles. You can rename, remove, or replace them. `sandbox build NAME` uses the `dockerfile` and `context` declared in each profile; `sandbox build preparer` remains a separate reserved core command. See [Configuration](configuration.md).
