# syntax=docker/dockerfile:1.7
# Optional NVIDIA CUDA + PyTorch runtime. The host must have a suitable NVIDIA
# driver and NVIDIA Container Toolkit configured for ROOTLESS Docker.
# CUDA 12.6 matches the pinned PyTorch cu126 wheel below.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

RUN rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git git-lfs openssh-client curl bash \
      python3 python3-venv build-essential ripgrep less jq tmux

# Store PyTorch in a dedicated base venv (same path as the XPU example),
# so sandbox-venv / sandbox-py can reuse it without a second torch install.
RUN python3 -m venv /opt/venv
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    /opt/venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cu126 \
      'torch==2.9.1'
COPY images/shared/sandbox-venv /usr/local/bin/sandbox-venv
RUN chmod 755 /usr/local/bin/sandbox-venv && mkdir -p /opt/venvs /workspace
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /workspace
CMD ["/bin/bash"]
