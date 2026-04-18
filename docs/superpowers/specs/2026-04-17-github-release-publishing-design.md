# Design: GitHub release publishing for HACS

## Problem

HACS reads version metadata from GitHub **releases**, not bare tags. The
`tag:auto` GitLab CI job creates a tag on GitLab and bumps
`manifest.json` / `pyproject.toml`, and the GitLab → GitHub push mirror
replicates the commit and tag to the read-only GitHub mirror. HACS
needs an actual release object against each tag; without one it shows
the commit hash in update notifications.

An initial implementation added that responsibility to `tag:auto`
itself: after tagging, the job polls the GitHub mirror for the tag,
then POSTs a release. The mirror sync was observed taking 5+ minutes;
the poll's timeout has already been widened from 3 minutes (`d75d488`)
to 15 (`30ee783`) chasing it. As of this spec, **zero GitHub releases
have been successfully created by that path** — the poll either times
out or the release POST fails, and the pipeline error surfaces after
the tag has already been created, leaving GitHub with tags but no
releases.

CI runners sitting idle on a poll loop is the wrong approach, and in
this repo it isn't working. This spec replaces the in-`tag:auto`
release logic with an event-driven design: a GitHub Actions workflow
on the mirror that fires when the mirror itself delivers the tag.

## Solution

A GitHub Actions workflow on the GitHub mirror, running on a
**5-minute cron schedule**. Each run enumerates `v*` tags, checks
which have no matching release, and creates one for each missing
tag. Idempotent, race-free (only ever acts on tags that already
exist on GitHub), self-healing (a missed tag is picked up on the
next scan). Uses the built-in `GITHUB_TOKEN` — no cross-system
token plumbing, no polling, no idle CI runners.

`tag:auto` becomes responsible only for what it can actually do
synchronously and locally: bump version files and create the GitLab
tag. The downstream chain (mirror → scheduled scan → release) runs
without holding any of `tag:auto`'s resources.

This re-introduces a narrow GitHub Actions surface, deliberately
scoped to release publishing. Test/lint CI continues to live in
GitLab (`5eae7ea` removed Actions for that purpose; this workflow
addresses a different concern that's natively a GitHub-side scan).

### Trigger evolution (why schedule, not `push: tags`)

The first implementation of this design used `push: tags: ['v*']`
on the assumption that mirror-delivered tag refs would trigger
workflow events on GitHub. Empirically they did not — tested with
both HTTPS+fine-grained-PAT and SSH+deploy-key mirror auth, tags
landed correctly on GitHub but GitHub never synthesized `push`
events for them. GitHub's behaviour here is under-documented and
appears specific to pushes from GitLab's mirror process. Since the
pull-based design (scheduled scan) is strictly more robust and the
latency cost is small (≤5 min to HACS's update view), we switched
rather than fight the event pipeline.

## Architecture

Single workflow file: `.github/workflows/release.yaml`. Committed to
the GitLab canonical repo, replicated to the GitHub mirror
(`aarontc/home-assistant-area-lighting`) by the existing push mirror,
where GitHub Actions picks it up. The workflow only ever runs on
GitHub.

GitLab itself ignores `.github/` — there's no GitLab CI integration to
disturb. The file lives in the canonical repo so it's versioned,
reviewed, and visible alongside the rest of the project.

### Triggers

- `schedule: cron: '*/5 * * * *'` — fires every 5 minutes. Each
  run scans tags and publishes any that are missing a release.
- `workflow_dispatch` — manual trigger. Inputs:
  - `tag` (optional): specific tag to (re)publish. Leave blank to
    behave exactly like a scheduled run (scan all tags).
  - `replace` (optional, default false): only honored when `tag`
    is set. Regenerates notes on an existing release.

### Permissions

`permissions: contents: write` on the workflow. Uses the
auto-provided `GITHUB_TOKEN` — no PAT, no secret to manage.

### Concurrency

`group: release-sync`, `cancel-in-progress: false`. A single fixed
group serializes all runs (scheduled + manual), so a long-running
scan never collides with the next cron tick; the follow-up just
queues behind.

## Workflow

```yaml
name: Publish GitHub releases

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:
    inputs:
      tag:
        description: 'Specific tag to (re)publish; leave blank to scan all tags'
        required: false
        type: string
        default: ''
      replace:
        description: 'Overwrite notes when a release already exists (only honored when `tag` is set)'
        required: false
        type: boolean
        default: false

permissions:
  contents: write

concurrency:
  group: release-sync
  cancel-in-progress: false

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Clone repo with full history
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          set -euo pipefail
          git init -q .
          git remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${{ github.repository }}.git"
          git fetch -q --tags origin
          git -c advice.detachedHead=false reset -q --hard origin/main

      - name: Publish missing releases
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          SINGLE_TAG: ${{ inputs.tag }}
          REPLACE: ${{ inputs.replace || 'false' }}
        run: |
          set -euo pipefail

          if [ -n "${SINGLE_TAG:-}" ]; then
            tags="$SINGLE_TAG"
          else
            tags="$(git tag --list 'v*' --sort=v:refname)"
          fi

          if [ -z "$tags" ]; then
            echo "No tags to process."
            exit 0
          fi

          for TAG in $tags; do
            exists=false
            gh release view "$TAG" >/dev/null 2>&1 && exists=true

            if $exists && { [ -z "${SINGLE_TAG:-}" ] || [ "$REPLACE" != "true" ]; }; then
              echo "Skipping $TAG (release exists; workflow_dispatch with replace=true regenerates)"
              continue
            fi

            prev="$(git describe --tags --abbrev=0 "${TAG}^" 2>/dev/null || true)"
            range="${prev:+${prev}..}${TAG}"
            subjects="$(git log "$range" --format='%s' \
              | grep -vE '^\(Patch\) release: bump version to ' || true)"

            {
              for sev in Major Minor Patch; do
                entries="$(echo "$subjects" | grep -E "^\(${sev}\) " || true)"
                if [ -n "$entries" ]; then
                  echo "### ${sev}"
                  echo "$entries" | sed -E "s/^\(${sev}\) /- /"
                  echo
                fi
              done

              other="$(echo "$subjects" | grep -vE '^\((Major|Minor|Patch)\) ' || true)"
              if [ -n "$other" ]; then
                echo "### Other"
                echo "$other" | sed 's/^/- /'
                echo
              fi

              if [ -n "$prev" ]; then
                echo "[Compare on GitLab](https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting/-/compare/${prev}...${TAG})"
              fi
            } > /tmp/notes.md

            flag=""
            case "$TAG" in *-*) flag="--prerelease" ;; esac

            if $exists; then
              gh release edit "$TAG" --notes-file /tmp/notes.md $flag
              echo "Updated release $TAG"
            else
              gh release create "$TAG" --title "$TAG" --notes-file /tmp/notes.md $flag
              echo "Created release $TAG"
            fi
          done
```

## Release notes format

Sections per severity, dropping the version-bump chore commit and
stripping the `(Major)/(Minor)/(Patch)` prefix from each subject. The
`(severity)` prefix is the same convention `tag:auto` already uses to
classify commits via the `versioning` package — no new metadata
required.

Example for `v0.6.4` (commits: `08d51c7 (Patch) release: bump …`,
`4d2f198 fix: only reconcile startup state when persisted state
exists`, `072d270 fix: reconcile persisted OFF state with physical
lights on startup`):

```markdown
### Other
- fix: only reconcile startup state when persisted state exists
- fix: reconcile persisted OFF state with physical lights on startup

[Compare on GitLab](https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting/-/compare/v0.6.3...v0.6.4)
```

The bump commit is dropped. The two `fix:` commits don't carry a
severity prefix, so they fall into "Other" — which is fine and
matches reality (they were patch-equivalent). Future commits with
explicit `(Major)/(Minor)/(Patch)` prefixes will sort into named
sections.

## Migration: removing the in-`tag:auto` release code

The new workflow replaces — not coexists with — the existing logic.
After the workflow is proven (live test on `v0.1.0` and backfill of
all existing tags, see Test plan), the following come out:

**`.gitlab-ci.yml`** — drop the `--github-token` and `--github-repo`
flags from the `dagger call create-tag` invocation in `tag:auto`,
and the matching CI/CD variable comments in the job header.

**`dagger/version.go`** — strip:

- The `githubToken` and `githubRepo` parameters on `CreateTag`, and
  the entire `if githubToken != nil && githubRepo != ""` block in
  its body.
- The standalone `CreateRelease` function. Backfills now happen via
  the workflow's `workflow_dispatch` (with optional `replace=true`),
  so a Dagger entry point for it is redundant.
- Helper functions used only by the above: `resolvePrevTag`,
  `buildReleaseBody`, `waitForGitHubTag`, `createGitHubRelease`.
- The `time` import if nothing else uses it.

**`CONTRIBUTING.md`** — drop the "Publishing GitHub Releases" section
(currently lines ~114–146, documents the GitLab `GITHUB_TOKEN`
variable and the standalone `CreateRelease` backfill recipe), and
the trailing sentence in "Tagging manually" about
`--github-token` / `--github-repo`.

**GitLab CI/CD variable `GITHUB_TOKEN`** — can be removed from the
GitLab project's CI/CD settings. Optional: the workflow doesn't use
it, so leaving it set has no functional effect, but removing it
reduces secrets sprawl. Out of scope for this implementation
(user-managed via UI).

### Migration ordering

The remove-from-`tag:auto` work happens **after** the new workflow
is proven and historical tags are backfilled. That sequencing means
there is never a window where neither path can produce releases —
even though the old path produces zero today, removing it before the
replacement is live would be a needless regression risk.

Migration is **not reversible per-commit** (removing helper
functions requires a recompile of the dagger module on the next CI
run), but the rollback path is simple: revert the migration commits
and the previous behavior — including the broken poll loop — comes
back. The workflow then keeps running in parallel; no harm.

## Decisions

- **Idempotency:** skip any tag that already has a release. The
  whole point of the scan design — every scheduled run is safe
  to re-invoke. `workflow_dispatch` with `tag=<X>` and
  `replace=true` is the one escape hatch for regenerating notes
  on an already-released tag.
- **Prerelease:** auto-flag any tag containing `-` (e.g.,
  `v0.7.0-rc1`) as prerelease. Cheap, future-proof.
- **Title:** bare tag (`v0.6.4`). GitLab tag messages use
  "Release vX.Y.Z" — minor inconsistency but `gh`'s default is the
  tag name, and HACS doesn't render the title prominently.
- **Notes script location:** inline in the workflow (~40 lines
  including the loop). If it grows or needs sharing with a Dagger
  function, extract to `scripts/build-release-notes.sh`. Not yet.
- **Schedule interval:** 5 minutes. Faster feels wasteful (most
  runs find nothing to do); slower pushes the latency to HACS past
  the subjectively-acceptable mark. GitHub may skew cron by up to
  ~15 min during peak load — not a blocker, just noting that "every
  5 minutes" is a best-effort floor.

## Error handling

- **Mirror sync in-flight:** the scan only processes tags
  `git tag --list` reports, which means tags already on GitHub.
  A tag mid-flight through the mirror just isn't seen this round
  and is picked up on the next. No runner waits for anything.
- **`gh release create` failure:** the step fails, the job fails,
  the next scheduled run retries. If the same tag fails twice in
  a row, there's a real problem (malformed notes, API outage) —
  rerun via `workflow_dispatch` with `tag=X` to get a focused log.
- **No previous tag (first release ever):** the script handles
  `prev=""` and produces notes against the full history. Edge case
  that matters only for `v0.1.0`.
- **Empty notes (no qualifying commits):** the file ends up
  containing just the GitLab compare link (or empty if no previous
  tag). `gh release create` accepts this.
- **Scheduled-workflow suspension:** GitHub disables scheduled
  workflows on repos with no activity for 60 days. The mirror
  itself is frequently updated by GitLab's mirror cron even without
  user activity, but if the repo ever does go dormant, schedules
  will need re-enabling via the Actions UI. Not something this
  design can prevent.

## Pre-conditions (verify before first run)

- The GitLab project's push mirror to
  `aarontc/home-assistant-area-lighting` is enabled and includes
  tags. Proven in practice — both HTTPS+fine-grained-PAT and
  SSH+deploy-key mirror setups successfully replicated tags.
- GitHub Actions is enabled on the mirror repo (Settings → Actions).
- The repo's "Actions permissions" allow `actions/*` (GitHub-owned
  actions) *or* the workflow uses only inline shell for
  checkout/API — this design uses inline `git` + `gh` CLI to avoid
  depending on `actions/checkout`, keeping the workflow compatible
  with strict action allowlists.

## Test plan

### Local verification (before commit)

1. Extract the notes-building bash into a tmp script.
2. Run against a handful of existing tags with varying histories
   (e.g., `v0.6.4`, `v0.6.1`, `v0.6.0`, `v0.5.0`).
3. Hand-inspect output for: bump commit dropped, severities grouped
   correctly, "Other" catches unprefixed commits, compare URL
   correct.
4. Verify `prev=""` path on `v0.1.0` (first tag, no predecessor —
   produces notes with no compare link).

### Live verification (after commit + mirror sync)

5. Confirm the workflow file appears on the GitHub mirror
   (`.github/workflows/release.yaml`).
6. Run `workflow_dispatch` with `tag` blank (scan mode) or with
   a specific tag to quickly populate releases without waiting
   for the next cron tick.
7. Inspect the resulting GitHub release for formatting.
8. Wait ~10 minutes (one or two cron ticks) and confirm the
   scheduled scan is idempotent — no duplicate releases, no
   failures, and new tags (if any) get picked up automatically.

### Regression coverage

- The notes script is too small to warrant a dedicated test
  framework. Local verification on existing tags is the test.
- If the script ever grows beyond ~40 lines, extract to
  `scripts/build-release-notes.sh` and add a Bats / shell-test
  harness.

## Out of scope

- Attaching artifacts to releases. HACS pulls source from the tag;
  no compiled binaries to attach.
- A Dagger function for release publishing. The workflow's only real
  logic is the notes script; wrapping bash in Dagger adds no value.
- Replacing the GitLab → GitHub push mirror with dual-push from CI.
  The mirror is fine; we're working with it, not around it.
- A `CHANGELOG.md`. Generated release notes serve the same purpose
  for users; maintaining a parallel hand-curated file would
  duplicate effort.
- Removing the `GITHUB_TOKEN` GitLab CI/CD variable. Done via the
  GitLab UI; safe to leave in place after migration.
- Improving the GitLab → GitHub mirror sync latency. The mirror's
  5+ minute delay is upstream behaviour; the workflow design
  tolerates it without holding any runner.
