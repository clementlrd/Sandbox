# syntax=docker/dockerfile:1.7
# Example external runtime; copy into ~/.config/sandbox/runtimes/node/
FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates bash ripgrep \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
CMD ["/bin/bash"]
