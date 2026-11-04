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
  - denylist scan if --anonymize (unless explicitly skipped)
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

`create` requires a clean source Git repository and explicitly built preparer
image. The preparer has read-only source access and a writable output session
directory; it runs without network access, Docker socket, host SSH credentials
or interactive agent. With `--anonymize`, historical author/committer/tagger
identities are replaced with a synthetic identity; the old-to-new commit map is
retained privately on the host. Git identity, remotes and object alternates are
validated during creation. The initial content denylist is checked unless
`create --anonymize --skip-denylist` is specified. This option does not suppress
identity rewriting or structural checks.

Git history and branches remain available inside the sandbox. Rewritten commit hashes differ from those in the source repository. The importer uses the host-only mapping to reconnect agent-created commits to source ancestry.

## Execution

Each `run` or `exec` launches a new, short-lived Docker container. The manager enforces rootless Docker and uses explicit bind mounts, named volumes, dropped Linux capabilities, a process limit, memory limit and `no-new-privileges`. The image's own filesystem changes are discarded on exit; mounted state persists.

No Git identity checks, denylist scans, or calls to the preparer are made by
`run` or `exec`. The sandbox can change its own Git remotes, branches, authors
and files after creation. User-supplied read-only mounts (for example skills)
and shared tools are **not scanned** by the one-time repository preflight; they
are independently selected at execution time. Re-examine those mounts before
exposing sensitive host content to an agent.

Application data is **not tied to a runtime**: one sandbox's app volumes are shared by its `base`, `python-xpu`, `python-cuda`, and custom runtimes. Python venv volumes **are tied to the exact image ID**, because interpreter paths and native package ABIs are generally not portable between images. Distinct profiles that reference the same image ID may share that venv volume.

## Import

`import` is a **host-side** action; no authenticated push or remote access is given to the agent. It fetches objects from the prepared local repository into a temporary ref namespace, translates original ancestors using the commit map and re-creates new commit objects using the host's configured Git identity. Multiple branch heads are imported under `refs/heads/sandbox/NAME/` for deliberate review. It does not change the source checkout or perform a merge or push.

The map is extended for every newly exported commit. Known mapped commits reuse
their exact host originals, rather than being re-authored on each import. All
local heads are exported, including unchanged branches; remote-tracking refs are
not exported as branches. Their reachable history is still translated when it is
an ancestor of a local head.

No importer can infer uncommitted work from a Git commit graph. Commit intended changes in the sandbox before importing. The importer has unit tests for branches and merges but needs end-to-end validation with unusual Git layouts, LFS, submodules, tags, and interrupted operations.

## Refresh and round trips

`refresh NAME --branch BRANCH` captures one explicit host local branch tip. It
does not require a clean source checkout and does not transfer working files.

The trusted preparer receives read-only mounts of the original source, existing
sandbox repository, and a private mapping snapshot. Output is staged privately
outside the runtime-mounted workspace. Raw host history is fetched only into an
intermediate bare store. Traversal stops at mapped host commits, whose existing
sandbox counterparts supply the translated ancestry. Unknown commits are
translated while retaining file trees, dates, and message bytes. Anonymized
refresh replaces author/committer identities and removes embedded signatures;
non-anonymized refresh preserves original commit bytes when parents are unchanged.

Only reachable prepared history is fetched into a fresh export repository. An
object-store check rejects unreachable leftovers. The raw intermediate store is
removed, and anonymized incoming history is screened before any transfer into the
live workspace. Existing mapped sandbox ancestry is excluded from renewed
identity/content screening; new commit objects, trees, blobs, tracked paths, and
the destination reference name are checked. No existing checkout is rescanned.

The host manager fetches from that clean export, not from the original source,
then publishes `refs/remotes/host/BRANCH`. It never sets a host remote URL or
modifies local sandbox branches, worktrees, or application state. A rewritten host
branch is reported as a non-fast-forward update. Users integrate changes with
ordinary Git merge, rebase, or cherry-pick operations.

Both directions extend the same host-only commit correspondence:

```text
Sandbox: a---b                 import records B <-> b
Host:    A---B---C             host accepts B and adds C
Sandbox: a---b---c             refresh reuses b; records C <-> c
Host:    A---B---C---D         next import reuses C; records D <-> d
```

Rewritten commits get new IDs and new pairs; old pairs are not substituted for
new branch history. Ambiguous correspondences are rejected rather than guessed.
Mappings are not Git references and cannot prevent garbage collection. Required
missing mapped objects produce an explicit error.

An instance-level advisory lock serializes import/refresh and prevents concurrent
destruction. Objects are transferred before mappings are atomically persisted;
only then are public references updated with expected-old-value checks. Failed
publication may leave extra valid mappings/objects, which a retry can reuse; it
does not require rolling back or discarding user history. No automatic archival
refs are created when `import --force` replaces review tips.

## Destruction

`destroy NAME --yes` removes the manager's instance directory and **all recorded sandbox volumes** after verifying there is no running labeled application container. It leaves original Git repos, shared host tools, images and BuildKit caches untouched. There is no built-in backup/export of application volumes, so treat it as irreversible.

## Operational boundaries

- `sandbox` deliberately does not manage application-specific process profiles or auto-run setup recipes. Enter the shell or use `exec`.
- Runtime images can be supplied by users; the core knows no programming language or GPU brand.
- Optional host recipes are a deliberate **privileged-on-host extension point**, distinct from the container runtime.
- Build actions are explicit. Changing the source repo cannot invalidate a built-in runtime image layer, because source is not used as a Docker build context.
