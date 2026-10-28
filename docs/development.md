# Development and testing

## Source layout

```text
bin/sandbox                    # user-facing launcher
libexec/sandboxlib/            # CLI, config, Git preparation/preflight/import
images/preparer/               # trusted no-network preparation worker
images/base/                   # optional general-purpose Ubuntu runtime
images/python-xpu/             # optional Intel XPU example runtime
images/python-cuda/            # optional NVIDIA CUDA example runtime
examples/config.toml           # default config, runtime examples
recipes/                       # trusted host-side setup scripts
tools/                         # sandbox-py (venv creation and pip) and optional OpenCode installer
tests/                         # unit/integration-like local Git tests
```

The preparer source is copied into its Docker image. The agent runtime receives **no host Git credential directory or Docker socket by default**.

## Run local tests

```bash
python -m compileall -q libexec/sandboxlib
python -m unittest discover -s tests -v
bash -n install.sh tools/install-opencode.sh recipes/*.sh
```

These tests cover configuration, Git history preparation/import, preflight, CLI argument construction, recipe confirmation and simulated pip reuse. Many tests execute *real local Git* operations. They do **not** launch rootless Docker or exercise a real GPU; those still need a separate integration test on Linux.

## Reproduce manually on Linux

Use a disposable, synthetic Git repository rather than your private source for the first end-to-end test.

```bash
./install.sh --no-tools
sandbox build preparer
sandbox build base
sandbox create smoke /path/to/clean/test-repo
sandbox exec smoke -- git status
sandbox run smoke
# In the sandbox: edit a file and commit it.
sandbox import smoke
sandbox destroy smoke --yes
```

Then test anonymized history with synthetic identities and a denylist; deliberately introduce a forbidden term into an old commit and ensure creation/launch is blocked. Test a custom runtime (`sandbox runtimes`, `sandbox build custom`, `sandbox run ... --runtime custom`), and an optional device-backed runtime on a host that actually has that device. Check app volume survival after an exit and deletion after `destroy`. Try setup recipes with synthetic credentials, never production tokens.

## Adding a new runtime

Usually **do not edit Python source**. Add `[runtimes.NAME]` in `config.toml`, reference a working Docker image, and optionally give its Dockerfile/context. See [Adding a runtime](runtimes.md). Contributions with a new example runtime may go in `examples/runtimes/` without making that runtime a mandatory package dependency.

A profile should not hardcode language or GPU checks into core `cli.py`. Device passthrough and per-image venv persistence belong to the configurable profile. New image recipes must preserve Docker layer/cache reuse and must not copy the user's repository into the build context.

## Changes to configuration and storage

The installer preserves user-maintained configuration, denylist and recipes. New images are *not* automatically substituted into existing sandboxes, which pin exact image IDs. Changes to the volume schema or Git import graph must be tested and documented with clear data-loss warnings.

## Roadmap

See [release-readiness](release-readiness.md). High-value next tasks: true rootless Docker end-to-end tests, a documented threat model reviewed by a second maintainer, improved cleanup/recovery, and more robust imported Git metadata/submodules/LFS handling. Avoid adding an app-specific plugin system unless a concrete repeated use case justifies it.

Test the strict configuration parser and generic runtime build resolution whenever those interfaces change.
