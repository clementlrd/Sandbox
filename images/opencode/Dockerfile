# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=sandbox/base:v1
FROM node:22-bookworm-slim AS node
FROM ${BASE_IMAGE}
# Node can be layered onto either CPU runtime or Intel XPU runtime.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ARG OPENCODE_VERSION=2.0.0
RUN --mount=type=cache,target=/root/.npm \
    npm install -g "@opencode/cli@${OPENCODE_VERSION}" && opencode --version
WORKDIR /workspace
CMD ["opencode"]
