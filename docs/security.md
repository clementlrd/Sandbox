# Security and privacy model

> **Not an audited isolation product.** This document describes intended protections, their limitations and what to review before accepting untrusted or hostile code. Do not advertise the tool as guaranteeing anonymity, preventing exfiltration or resisting kernel-level attacks.

## Trust boundaries

| Component | Trust assumption |
| --- | --- |
| Host CLI, installer and setup recipes | **Trusted host code**; can read host files and credentials |
| Preparation container | Trusted code that receives a read-only Git source and a private denylist |
| Runtime container | May run arbitrary untrusted or agent-generated commands; isolated with rootless Docker |
| Configured runtime Dockerfile/image | **Trusted input** for the user; the runtime image determines code and libraries that execute |
| Repository | Agent can read/edit/delete the sandbox's independent clone |
| Shared tools | Host-managed binaries mounted RO, but agent can inspect/read/execute them |
| Persistent app volumes | Writable by the agent, potentially contain OAuth tokens, credentials and sessions |

The Docker socket, host SSH agent and host Git credentials are not mounted by default. The *original repository* is visible only to the preparation container. Agents get a prepared repository with no original remote configured. None of this prevents network uploads; unrestricted outbound networking lets an agent send code elsewhere, including with HTTP GET queries.

## What anonymization does and does not do

With `--anonymize`, the preparer rewrites author/committer/tagger identity metadata and stores the old/new Git commit mapping **outside** the runtime mount. A private user-defined denylist scans reachable/unreachable Git objects, current filesystem paths, symlink targets and configured host read-only mounts. Matches fail closed before launch. The same preflight is repeated on later launches.

It does **not** guarantee privacy. Users must consider: personal details in commit messages, names not on the list, zipped/compressed/encrypted/encoded data, images, Git LFS/submodules, files ignored by Git, binary false positives, credentials already persisted in app volumes, runtime tools and their metadata, and information inferred from historical behavior. The preflight intentionally skips shared tools content because arbitrary executable bytes can cause spurious matches. For strict confidentiality, sanitization needs independent review beyond this best-effort scanner.

A non-anonymized sandbox still has an independent Git repository but **preserves historical identities**. The `--anonymize` option affects creation and cannot be switched later in place.

## Host executable and mounted-skill risks

Setup recipes under `~/.config/sandbox/recipes` run **on the host** after explicit confirmation; never copy an untrusted repo recipe there without review. User-provided Dockerfiles may execute arbitrary build instructions. `[[mounts]]` can expose host information; they are intentionally read-only and scanned for configured denylist terms in anonymized mode, but content in them is still visible to the agent.

Avoid mounts containing secrets, host SSH keys, browser profiles, Docker sockets, private home directories or writable shared binaries. Sharing a Docker socket with an agent typically destroys the intended isolation boundary.

## Credentials

Application auth/session state persists in sandbox-specific volumes. This makes browser login or host-to-sandbox credential import convenient, **but the agent can potentially read its own credentials**. Keep scopes minimal, avoid importing all host credentials, and destroy/rotate credentials as needed. Never commit tokens, denylist files or real credentials to a public repository. Remember that destroying the sandbox removes the auth volumes.

## Docker and GPU security

Rootless Docker executes the daemon and containers without host root privileges; it is not equivalent to a microVM. Runtime device passthrough exposes additional host device interfaces; NVIDIA `--gpus` requests additionally require a host driver and configured NVIDIA Container Toolkit. Rootless GPU device/ACL mappings can differ by host. `--cap-drop ALL`, `--security-opt no-new-privileges`, memory and PID limits are defense in depth, not a promise that a malicious workload is safe.

The current CLI runs containers as UID 0 inside the *rootless user namespace*, sets `HOME=/root` and supports only Linux-compatible images with a functional `/bin/sh`. It is not yet designed to support every `USER`, entrypoint, distro or GPU configuration.

## Security review checklist for maintainers

Before a broad public release, review at least: filesystem mount target collisions, symlink traversal, Git configuration/hooks/filters, import/update-ref safety, historical and non-text leaks, denial-of-service resource exhaustion, race conditions in setup/destroy, credential handling (stdin/temporary files), downloaded binary verification, third-party Dockerfile build contexts, and the behavior of rootless device mappings. Run container-based end-to-end tests rather than relying solely on mocks.

References: [Docker rootless mode](https://docs.docker.com/engine/security/rootless/), [Docker rootless limitations](https://docs.docker.com/engine/security/rootless/troubleshoot/).

## Explicit denylist bypass

`create --anonymize --skip-denylist` is an **opt-out from content screening**
that persists for the named sandbox. `run --skip-denylist` and
`exec --skip-denylist` bypass only the specified invocation. The Git history
rewrite is still performed on creation, and launch still rejects unexpected
Git remotes, non-synthetic reachable commit authors/committers, and object
alternates. **It does not check source files, history blobs, skills, mount
contents, or filenames for sensitive strings.** A copyright name in a license
will still be visible to the agent, and other secrets may be present too.

Use strict checking by default; bypass it only when you explicitly accept
that disclosure. This is deliberately not a silent fallback on scan failures.
