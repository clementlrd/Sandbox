# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
# Keep package downloads in BuildKit's cache; image contains installed packages.
RUN rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git git-lfs openssh-client curl bash python3 \
      python3-pip python3-venv python-is-python3 build-essential ripgrep less jq tmux
RUN mkdir -p /workspace /opt/venvs
WORKDIR /workspace
CMD ["/bin/bash"]
