# Contributing

Thanks for taking the time to contribute. This project uses [Dagger](https://dagger.io/)
to pin the exact CI environment, so the commands you run locally are the same
ones GitLab CI runs.

## Prerequisites

You only need **Dagger** on your `PATH`. Dagger spins up a Python 3.13
container, installs `uv`, and runs everything inside it — so you don't need
Python, `uv`, `ruff`, `mypy`, or `pytest` installed on your host.

- Dagger: `v0.20.5` (see `.tool-versions`)
  - Install: <https://docs.dagger.io/install> or `asdf install` if you use asdf
- Docker (or another OCI runtime) must be running for Dagger to spin up containers

## Running the full check suite

Run the same pipeline CI runs on merge requests:

```sh
dagger call all
```

That runs lint, typecheck, the pytest suite, and the versioning-helper
tests concurrently and fails on the first error. Please run it before every
commit.

## Running individual checks

| Task                       | Command                         |
| -------------------------- | ------------------------------- |
| Lint (ruff check + format) | `dagger call lint`              |
| Typecheck (mypy)           | `dagger call typecheck`         |
| Unit + integration tests   | `dagger call test`              |
| Tests against latest HA    | `dagger call test-latest`       |
| Versioning-helper tests    | `dagger call test-versioning`   |

The first run of each pulls the Python image; subsequent runs reuse the cached
`uv` volume and are much faster.

## Git hooks

Two hooks live in `hooks/`:

- `pre-commit` runs `dagger call lint` and aborts the commit on failure.
- `commit-msg` enforces that the commit subject starts with `(Major)`,
  `(Minor)`, or `(Patch)` — the markers the auto-versioning pipeline reads
  (see [Versioning](#versioning)). Merge/fixup/squash/revert subjects are
  exempt.

Enable both **once per clone** by pointing Git at the in-repo hooks
directory:

```sh
git config core.hooksPath hooks
```

To skip the hooks for one commit (discouraged), pass `--no-verify`.

If the `pre-commit` lint step is too slow for your workflow, run
`dagger call all` manually before each commit and unset just the
hooks path:

```sh
git config --unset core.hooksPath
```

(The `commit-msg` hook is cheap — there's no reason to disable it.)

## Versioning

Releases use semantic versioning and are driven entirely by commit
messages. Every commit subject must start with one of these markers
(enforced by the `commit-msg` hook):

| Marker     | Effect            | Example                                                |
| ---------- | ----------------- | ------------------------------------------------------ |
| `(Major)`  | `X.y.z → X+1.0.0` | `(Major) drop Python 3.12 support`                    |
| `(Minor)`  | `x.Y.z → x.Y+1.0` | `(Minor) ci: auto-tag main branch`                     |
| `(Patch)`  | `x.y.Z → x.y.Z+1` | `(Patch) area_lighting: fix motion timer on HA reload` |

Keep the subject under ~72 chars. An optional area prefix
(`area_lighting:`, `ci:`, `docs:`, `test:`, …) may follow the severity
marker. The highest marker across all commits since the last tag wins.

### Previewing the next release

```sh
dagger call commits-since-tag   # list commits and the severity each contributes
dagger call next-version        # print the version the next release would get
```

### Cutting a release

Releases are tagged automatically by CI. The `tag:auto` GitLab CI job
runs on every push to `main`, calculates the next version from commit
subjects, and creates the tag via the GitLab API.

For this to work, a **project CI/CD variable `PROJECT_ACCESS_TOKEN`**
must be set to a Project Access Token (or Personal Access Token) that
has the **`write_repository`** scope. Create it under **Settings →
Access Tokens** and mark the variable **Masked** and **Protected**.

The job is a no-op on pipelines triggered by tags themselves, so there's
no feedback loop.

### Tagging manually

You can also invoke the same Dagger function locally — useful for
testing or to tag from a detached branch:

```sh
export GITLAB_TOKEN=glpat-…
dagger call create-tag \
    --source=. \
    --gitlab-url=https://gitlab.idleengineers.com \
    --project-id=aaron/home-assistant-area-lighting \
    --token=env:GITLAB_TOKEN
```
