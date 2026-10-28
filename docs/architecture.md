# Architecture and lifecycle

`sandbox` is a **local sandbox manager**, not an application runner or Docker orchestrator. It intentionally keeps three responsibilities separate: preparing a Git workspace, selecting a Docker runtime, and preserving state across ephemeral executions.

```text
Host Git checkout (never mounted in the agent container)
           |
           v
Trusted preparer container --network none
  - independent clone, no object alternates
  - optional author/committer/tagger rewrite
  - host-only commit map
  - denylist scan if --anonymize
           |
           v
Host state: ~/.local/state/sandbox/instances/NAME/
  - repo/          <------> /workspace        (writable)
  - commit-map             (not runtime-mounted)
  - metadata.json          (not runtime-mounted)
           |
           v
Ephemeral rootless Docker container, --rm
  - selected runtime image (pinned on first use)
  - optional /dev device nodes
  - /opt/tools           (shared read-only tools directory)
  - /root/.local/share   (per-sandbox volume)
  - /root/.config        (per-sandbox volume)
  - /root/.cache         (per-sandbox volume)
  - /opt/venvs           (per-sandbox+image-ID volume, if enabled)
           |
           v
Host-only import process -> review branches in original Git repo
```

## Creation

`create` requires a clean source Git repository and explicitly built preparer image. The preparer has read-only source access and a writable output session directory; it runs without network access, Docker socket, host SSH credentials or interactive agent. For `--anonymize`, Git commit author/committer (and tagger) identity fields are rewritten to a synthetic identity and a private old-to-new commit map is retained on the host. Anonymization and denylist scanning are **optional per named sandbox**, but scanning is **mandatory** for sandboxes created with `--anonymize`.

Git history and branches remain available inside the sandbox. Rewritten commit hashes differ from those in the source repository. The importer uses the host-only mapping to reconnect agent-created commits to source ancestry.

## Execution

Each `run` or `exec` launches a new, short-lived Docker container. The manager enforces rootless Docker and uses explicit bind mounts, named volumes, dropped Linux capabilities, a process limit, memory limit and `no-new-privileges`. The image's own filesystem changes are discarded on exit; mounted state persists.

For anonymized sandboxes, a preparer preflight runs again before an application container starts. It checks the workspace and configured read-only mounts. It does not inspect globally shared tools binaries or the contents of previously persisted application data/venv volumes. See [Security](security.md) for why that matters.

Application data is **not tied to a runtime**: one sandbox's app volumes are shared by its `base`, `python-xpu` and custom runtimes. Python venv volumes **are tied to the exact image ID**, because interpreter paths and native package ABIs are generally not portable between images. Distinct profiles that reference the same image ID may share that venv volume.

## Import

`import` is a **host-side** action; no authenticated push or remote access is given to the agent. It fetches objects from the prepared local repository into a temporary ref namespace, translates original ancestors using the commit map and re-creates new commit objects using the host's configured Git identity. Multiple branch heads are imported under `refs/heads/sandbox/NAME/` for deliberate review. It does not change the source checkout or perform a merge or push.

No importer can infer uncommitted work from a Git commit graph. Commit intended changes in the sandbox before importing. The importer has unit tests for branches and merges but needs end-to-end validation with unusual Git layouts, LFS, submodules, tags, and interrupted operations.

## Destruction

`destroy NAME --yes` removes the manager's instance directory and **all recorded sandbox volumes** after verifying there is no running labeled application container. It leaves original Git repos, shared host tools, images and BuildKit caches untouched. There is no built-in backup/export of application volumes, so treat it as irreversible.

## Operational boundaries

- `sandbox` deliberately does not manage application-specific process profiles or auto-run setup recipes. Enter the shell or use `exec`.
- Runtime images can be supplied by users; the core knows no programming language or GPU brand.
- Optional host recipes are a deliberate **privileged-on-host extension point**, distinct from the container runtime.
- Build actions are explicit. Changing the source repo cannot invalidate a built-in runtime image layer, because source is not used as a Docker build context.
