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

A GitHub Actions workflow on the GitHub mirror, triggered by the tag
push the mirror itself performs. The workflow runs only when the tag
arrives on GitHub (so it can never race the mirror), takes seconds,
and uses the built-in `GITHUB_TOKEN` — no cross-system token plumbing,
no polling, no idle CI runners. The wait for the mirror sync still
exists, but it's silent: nothing is consuming a runner during it.

`tag:auto` becomes responsible only for what it can actually do
synchronously and locally: bump version files and create the GitLab
tag. The downstream chain (mirror → workflow → release) runs without
holding any of `tag:auto`'s resources.

This re-introduces a narrow GitHub Actions surface, deliberately
scoped to release publishing. Test/lint CI continues to live in
GitLab (`5eae7ea` removed Actions for that purpose; this workflow
addresses a different concern that's natively event-driven on
GitHub).

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

- `push: tags: ['v*']` — fires when the mirror lands a new
  semver-style tag.
- `workflow_dispatch` — manual trigger for backfilling existing
  un-released tags and for regenerating notes after a logic change.
  Inputs:
  - `tag` (required): the tag to (re)publish, e.g. `v0.6.4`.
  - `replace` (optional, default false): overwrite the existing
    release's notes if a release already exists.

### Permissions

`permissions: contents: write` on the workflow. Uses the
auto-provided `GITHUB_TOKEN` — no PAT, no secret to manage.

### Concurrency

`group: release-${{ inputs.tag || github.ref_name }}`,
`cancel-in-progress: false`. Prevents a manual dispatch and an
inbound mirror push from racing on the same tag.

## Workflow

```yaml
name: Publish GitHub release on tag

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:
    inputs:
      tag:
        description: 'Tag to (re)publish (e.g. v0.6.4)'
        required: true
        type: string
      replace:
        description: 'Overwrite notes if a release already exists'
        required: false
        type: boolean
        default: false

permissions:
  contents: write

concurrency:
  group: release-${{ inputs.tag || github.ref_name }}
  cancel-in-progress: false

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Resolve tag
        id: tag
        run: |
          ref="${{ inputs.tag || github.ref_name }}"
          echo "ref=$ref" >> "$GITHUB_OUTPUT"

      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          ref: ${{ steps.tag.outputs.ref }}

      - name: Build release notes
        env:
          TAG: ${{ steps.tag.outputs.ref }}
        run: |
          set -euo pipefail
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

      - name: Detect prerelease
        id: pre
        env:
          TAG: ${{ steps.tag.outputs.ref }}
        run: |
          if [[ "$TAG" == *-* ]]; then
            echo "flag=--prerelease" >> "$GITHUB_OUTPUT"
          else
            echo "flag=" >> "$GITHUB_OUTPUT"
          fi

      - name: Create or update release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAG: ${{ steps.tag.outputs.ref }}
          REPLACE: ${{ inputs.replace || 'false' }}
        run: |
          set -euo pipefail
          if gh release view "$TAG" >/dev/null 2>&1; then
            if [ "$REPLACE" = "true" ]; then
              gh release edit "$TAG" \
                --notes-file /tmp/notes.md \
                ${{ steps.pre.outputs.flag }}
              echo "Updated existing release $TAG"
            else
              echo "Release $TAG already exists; skipping (workflow_dispatch with replace=true to overwrite)"
            fi
          else
            gh release create "$TAG" \
              --title "$TAG" \
              --notes-file /tmp/notes.md \
              ${{ steps.pre.outputs.flag }}
          fi
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

- **Idempotency:** skip if the release already exists. Mirror
  retries, accidental re-pushes, and double-dispatches become no-ops.
  `workflow_dispatch` with `replace=true` is the explicit escape
  hatch for regenerating notes.
- **Prerelease:** auto-flag any tag containing `-` (e.g.,
  `v0.7.0-rc1`) as prerelease. Cheap, future-proof.
- **Title:** bare tag (`v0.6.4`). GitLab tag messages use
  "Release vX.Y.Z" — minor inconsistency but `gh`'s default is the
  tag name, and HACS doesn't render the title prominently.
- **Notes script location:** inline in the workflow (~25 lines). If
  it grows or needs sharing with a Dagger function, extract to
  `scripts/build-release-notes.sh`. Not yet.

## Error handling

- **Mirror sync race (tag arrives before its commit):**
  `actions/checkout` with `ref: <tag>` fails loudly. Acceptable —
  GitLab push mirrors send commits before tags, so this should never
  happen; if it does, we want to know and the failure is non-silent.
- **`gh release create` failure (other than "exists"):** job fails
  and is retryable via `workflow_dispatch`.
- **No previous tag (first release ever):** the script handles
  `prev=""` and produces notes against the full history. Edge case
  for backfilling `v0.1.0`.
- **Empty notes (no qualifying commits):** the file ends up
  containing just the GitLab compare link (or empty if no previous
  tag). `gh release create` accepts this.

## Pre-conditions (verify before first run)

- The GitLab project's push mirror to
  `aarontc/home-assistant-area-lighting` is enabled, push-mode, and
  includes tags. **Already proven** — the existing 15-minute poll
  in `waitForGitHubTag` does eventually find tags on GitHub, just
  slowly. Re-verify at execution time with:

  ```sh
  git ls-remote https://github.com/aarontc/home-assistant-area-lighting refs/tags/v0.8.1
  ```

  Should return the same SHA as `git rev-parse v0.8.1` locally.

- GitHub Actions is enabled on the mirror repo (default for new
  repos; verify in repo Settings → Actions). The mirror has had a
  workflow file in the past (`b52cb9e` added one, `5eae7ea` removed
  it), so Actions runs there — but confirm anyway.

## Test plan

### Local verification (before commit)

1. Extract the notes-building bash into a tmp script.
2. Run against `v0.6.4`, `v0.6.3`, `v0.6.2`, `v0.6.1`, `v0.6.0`,
   `v0.5.2` — sample of tags with varying commit counts and severity
   distributions.
3. Hand-inspect output for: bump commit dropped, severities grouped
   correctly, "Other" catches unprefixed commits, compare URL
   correct.
4. Verify `prev=""` path on `v0.1.0` (first tag, no predecessor).

### Live verification (after commit + mirror sync)

5. Confirm the workflow file appears on the GitHub mirror
   (`.github/workflows/release.yaml`).
6. Run `workflow_dispatch` with `tag=v0.1.0` first (oldest, smallest
   blast radius — and it has no release yet, so it's a real backfill).
7. Inspect the resulting GitHub release for formatting.
8. Backfill remaining tags (`v0.2.0` through `v0.8.1`) via
   `workflow_dispatch`. 17 tags, mechanical. (Zero releases exist
   today, so all historical tags are backfills.)
9. The next real release (when `tag:auto` cuts a new tag) should
   trigger the workflow automatically and produce a release within
   ~30 seconds of the mirror push *delivering* the tag (which itself
   may take several minutes — but no CI runner is held during that
   window).

### Regression coverage

- The notes script is too small to warrant a dedicated test
  framework. Local verification on existing tags is the test.
- If the script ever grows beyond ~30 lines, extract to
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
