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

## Pre-commit hook

A `pre-commit` hook that runs `dagger call lint` lives in `hooks/pre-commit`.
To enable it, point Git at the in-repo hooks directory **once per clone**:

```sh
git config core.hooksPath hooks
```

From then on, every `git commit` will run the lint step and abort the commit
if it fails. To skip the hook for a specific commit (discouraged), pass
`--no-verify`.

If the hook is too slow for your workflow, run `dagger call all` manually
before each commit instead and unset the config:

```sh
git config --unset core.hooksPath
```

## Commit message style

Short subject line (under ~72 chars), lowercase prefix describing the area
(`area_lighting:`, `ci:`, `docs:`, `test:`, etc.), then a blank line and a
longer body if needed. Run `git log` for recent examples.

## Versioning

Releases use semantic versioning and are driven entirely by commit
messages. A commit that starts with one of the following markers bumps
the corresponding component of the next release tag:

| Marker     | Effect          | Example                                   |
| ---------- | --------------- | ----------------------------------------- |
| `(Major)`  | `X.y.z → X+1.0.0` | `(Major) drop Python 3.12 support`      |
| `(Minor)`  | `x.Y.z → x.Y+1.0` | `(Minor) add per-area holiday scenes`   |
| `(Patch)`  | `x.y.Z → x.y.Z+1` | `(Patch) fix motion timer race on HA reload` |

Normal commits (no marker) count as a patch bump. The highest marker
across all commits since the last tag wins.

### Previewing the next release

```sh
dagger call commits-since-tag   # list commits and the severity each contributes
dagger call next-version        # print the version the next release would get
```

### Cutting a release

Releases are tagged via the GitLab API — no local `git tag` push is
needed. Create a Project Access Token (or use a Personal Access Token)
with **write_repository** scope, export it, then run:

```sh
export GITLAB_TOKEN=glpat-…
dagger call create-tag \
    --source=. \
    --gitlab-url=https://gitlab.idleengineers.com \
    --project-id=aaron/home-assistant-area-lighting \
    --token=env:GITLAB_TOKEN
```

The command calculates the next version, creates the tag at the current
`HEAD` commit, and prints the tag name. GitLab then triggers any
tag-scoped CI jobs (releases, HACS artifact, etc.).
