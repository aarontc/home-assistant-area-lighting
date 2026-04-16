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

That runs lint, typecheck, and the pytest suite concurrently and fails on
the first error. Please run it before every commit.

## Running individual checks

| Task                      | Command                         |
| ------------------------- | ------------------------------- |
| Lint (ruff check + format) | `dagger call lint`              |
| Typecheck (mypy)          | `dagger call typecheck`         |
| Unit + integration tests  | `dagger call test`              |
| Tests against latest HA   | `dagger call test-latest`       |

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
