# Continuous integration (GitHub Actions)

The repository includes [`.github/workflows/tests.yml`](../.github/workflows/tests.yml).
It runs automatically after a push or pull request, and can be started manually
from the Actions tab using **Run workflow** (`workflow_dispatch`).

## What runs

GitHub starts three independent jobs on `ubuntu-latest`, one for each supported
Python version: **3.11, 3.12 and 3.13**.

Each job:

1. Checks out the repository (`actions/checkout@v4`).
2. Installs the matrix Python version (`actions/setup-python@v5`).
3. Compiles `libexec/sandboxlib` as a Python syntax check and runs Bash syntax
   checks on the installer and shipped recipes.
4. Executes `python -m unittest discover -s tests -v`.

Tests use temporary Git repositories, mocked Docker calls and simulated pip
behavior. **No live rootless Docker containers, GPU, credentials, or published
artifacts are involved.** A green check does not certify that a runtime can
start on your host or that the isolation boundary has been audited.

## What you need to configure

For a normal public GitHub repository, **nothing beyond pushing the workflow
file**. The workflow requires no secrets, no self-hosted runner, no Docker
service, and no API key. It requests only `contents: read` permissions for the
job's automatically provided `GITHUB_TOKEN`.

If no runs appear, check **Repository Settings → Actions → General** to see
whether Actions and the referenced public actions are allowed. Organization
policies can also restrict them. The **Actions** tab shows each job's logs.

Optional: once you have a reliable green run, add a branch ruleset requiring
these checks before merging pull requests. Don't require a GPU test that the
workflow doesn't provide.

## Run the same tests locally

From the repository root:

```bash
python -m compileall -q libexec/sandboxlib
bash -n install.sh tools/install-*.sh recipes/*.sh
python -m unittest discover -s tests -v
```

To validate behavior that CI cannot cover, use a rootless Linux Docker daemon
and follow [Development and testing](development.md#reproduce-manually-on-linux).

Before making a repository public, independently review the committed content
and history for secrets. The sandbox's denylist preflight serves anonymized
runtime preparation; it is not a publication or contributor-name check.
