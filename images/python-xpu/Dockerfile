# syntax=docker/dockerfile:1.7
# Intel-provided Ubuntu 24.04 compute runtime + torch XPU installed in /opt/venv.
# Pin the versioned upstream tag; docker build --pull controls base updates.
FROM intel/pytorch:xpu-2.14.0-ubuntu24.04
RUN rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git git-lfs openssh-client curl bash \
      python3-venv build-essential ripgrep less jq tmux
COPY images/python-xpu/sandbox-venv /usr/local/bin/sandbox-venv
RUN chmod 755 /usr/local/bin/sandbox-venv && mkdir -p /opt/venvs /workspace
WORKDIR /workspace
CMD ["/bin/bash"]
