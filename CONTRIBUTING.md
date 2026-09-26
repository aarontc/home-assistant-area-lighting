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

Avoid the literal string `skip ci` (or `ci skip`) anywhere in the
subject or body — GitLab matches those markers to suppress the
pipeline, and `tag:auto` won't run. If you need to refer to the
marker in prose, write it as `"skip&nbsp;ci"` or break it across
words.

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

### Publishing GitHub releases

GitHub is a push mirror of this GitLab project, and is in the release chain
only because HACS installs from there. GitLab stays the source of truth and
now drives the release too: the `release:github` job runs in the `release`
stage of the same `main` pipeline that tagged the version, and publishes a
GitHub Release for every `vX.Y.Z` tag that does not already have one. Notes
are built from the `(Major)/(Minor)/(Patch)` commit subjects in that tag's
range, with merge commits and the automated version-bump commit excluded.

For this to work, a **project CI/CD variable `GITHUB_RELEASE_TOKEN`** must
be set to a GitHub token with **`contents: write`** on the mirror
repository. A fine-grained personal access token scoped to that single
repository is the least-privilege option. Mark the variable **Masked** and
**Protected**. The mirror repository itself is set in `.gitlab-ci.yml` as
`GITHUB_MIRROR_REPO`, so it needs no UI configuration.

The job waits for the push mirror to carry the new tag to GitHub (up to 10
minutes) and checks that the tag resolves to the same commit GitLab has,
before publishing. That check is not ceremony: GitHub's create-release API
will invent a missing tag from the default branch tip, so publishing
without it could point a release at the wrong commit whenever the mirror
lagged. If the tag never arrives, the job fails — which is the signal that
the mirror is broken.

Because it scans every tag rather than just the newest, the job also
back-fills anything previously missed, and is idempotent: tags that already
have a release are skipped.

`release:audit` is the safety net. It performs the same scan with
`--check-only`, reporting and **failing** on any tag without a release
instead of publishing it. That covers the case where `release:github` never
ran at all (pipeline cancelled, runner outage, a hand-made tag), and makes
the gap visible rather than letting it sit unnoticed.

> **A pipeline schedule must exist for the audit — and for `nightly` — to
> run at all.** Neither runs on pushes. Create one under **Build → Pipeline
> schedules** targeting `main`; daily is plenty. Without a schedule both
> jobs are simply inert, with nothing to indicate it.

Scheduled pipelines are branch pipelines, so `CI_COMMIT_BRANCH` is set to
`main` on them. `tag:auto` and `release:github` therefore carry an explicit
`when: never` for `$CI_PIPELINE_SOURCE == "schedule"`. Without it `tag:auto`
would run on every scheduled pipeline and fail with "no commits since
&lt;tag&gt;" whenever `main` is already sitting on the last release's bump
commit — and because a failed job skips all later stages, that failure would
take `release:audit` and `nightly` down with it on precisely the quiet nights
they exist for. A scheduled pipeline runs `check`, then `release:audit`, then
`nightly`.

You can run either locally:

```sh
export GITHUB_TOKEN=github_pat_…
dagger call publish-github-releases \
    --source=. \
    --repo=aarontc/home-assistant-area-lighting \
    --token=env:GITHUB_TOKEN \
    --gitlab-project-url=https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting

# audit only; needs no token against a public repository
dagger call publish-github-releases \
    --source=. \
    --repo=aarontc/home-assistant-area-lighting \
    --check-only=true
```

Note that `--source=.` needs a real `.git` directory, so this does not work
from inside a `git worktree` (where `.git` is a file pointing elsewhere) —
run it from a normal clone.

The mirror must authenticate over **SSH** (deploy key). An HTTPS personal
access token would need the `workflow` scope just to push changes under
`.github/workflows/`; SSH avoids that.

**Why this does not use GitHub Actions.** Publishing used to be a GitHub
Actions workflow triggered by the mirror's tag push. Deploy-key pushes are
exempt from GitHub's recursion guard, so that trigger should be reliable —
but in practice it fired for v1.1.1 and silently did not for v1.2.0,
leaving a tag on GitHub with no release, no failed job, and no notification.
An intermittent trigger with no failure signal is worse than no trigger at
all, so the release moved to the pipeline that already owns tagging.
