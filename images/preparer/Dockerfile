# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm
RUN rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends git git-filter-repo ca-certificates
COPY libexec/sandboxlib /opt/sandbox/sandboxlib
ENV PYTHONPATH=/opt/sandbox
ENTRYPOINT ["python3", "-m", "sandboxlib.prepworker"]
