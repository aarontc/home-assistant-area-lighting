# GitHub release publishing for HACS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a GitHub release for every `vX.Y.Z` tag that lands on the GitHub mirror, so HACS shows real version numbers (not commit hashes) in update notifications. Replaces the existing in-`tag:auto` poll-and-release code (which has produced zero successful releases despite a 15-minute timeout).

**Architecture:** A single GitHub Actions workflow (`.github/workflows/release.yaml`) committed to the GitLab canonical repo, replicated to the GitHub mirror by the existing push mirror, triggered on `push: tags: ['v*']`. The workflow generates severity-grouped release notes from `git log <prev>..<tag>`, drops the chore version-bump commit, and calls `gh release create`. No coordination with GitLab CI; the auto-provided `GITHUB_TOKEN` is the only credential. After the workflow is proven and historical tags are backfilled, the old in-`tag:auto` release code (`dagger/version.go` helpers, `--github-token`/`--github-repo` flags) comes out — see Task 7.

**Tech Stack:** GitHub Actions, bash, `gh` CLI (pre-installed on Actions runners), `actions/checkout@v4`. Local validation uses bash + `git`. The migration step (Task 7) edits Go in the Dagger module and verifies with `gofmt` (and `dagger call all` if locally available).

**Spec:** `docs/superpowers/specs/2026-04-17-github-release-publishing-design.md`

---

## File structure

| File | Responsibility | Change |
| --- | --- | --- |
| `.github/workflows/release.yaml` | The workflow: trigger, notes generation, release create/update | Create |
| `.gitlab-ci.yml` | Drop `--github-token` / `--github-repo` flags from `tag:auto` and the matching variable comments | Modify |
| `dagger/version.go` | Strip GitHub release code: `CreateTag` parameters + inline block, `CreateRelease`, helpers, `time` import | Modify |
| `CONTRIBUTING.md` | Drop "Publishing GitHub Releases" section + the trailing GitHub flag note in "Tagging manually" | Modify |
| `TODO.md` | Mark "HACS version display" item complete | Modify |
| `/tmp/build-release-notes.sh` (ephemeral) | Local fixture-based validation of the notes-generation bash before embedding in YAML | Create + delete |
| `/tmp/expected-*.md`, `/tmp/actual-*.md` (ephemeral) | Test fixtures for local validation | Create + delete |

The notes-generation bash lives **inline in the workflow**, not as a tracked script. The local `/tmp/` script exists only to iterate on the bash without re-running CI.

---

## Notes for the implementer

- **Commit-message format.** This repo enforces `(Major|Minor|Patch) <subject>` via the `commit-msg` hook (`hooks/commit-msg`, active if `git config core.hooksPath hooks`). All commits in this plan use `(Patch)` — release tooling, not user-visible behavior.
- **No Python or pytest changes.** This plan touches one workflow YAML, the GitLab CI file, the Dagger Go module, and a couple of docs. The existing pytest suite is unaffected.
- **Dagger module verification.** Task 7 modifies `dagger/version.go`. There are no Go unit tests for these helpers (only `dagger/versioning/versioning_test.go` covers the pure version-parsing helpers, which this plan doesn't touch). Validation is `gofmt -d` for syntax and, if Dagger is locally available, `dagger call all` to exercise the call surface. Otherwise the next CI run on push catches any regression.
- **GitHub access from local machine.** The `gh` CLI is **not installed** locally for this user (and no plan to install it). Use the GitHub web UI for `workflow_dispatch` and for inspecting runs and releases.
- **Mirror sync timing.** The GitLab→GitHub push mirror has been observed taking 5+ minutes to deliver tags. The existing `waitForGitHubTag` was widened to 15 minutes chasing this. The new design tolerates the latency without holding any runner. When a verification step says "wait for the mirror," budget for several minutes, not seconds.
- **Pre-condition is load-bearing.** Task 1 verifies the GitLab→GitHub mirror pushes tags. The existing impl proves it does (eventually); re-verify anyway.

---

## Task 1: Verify pre-conditions

**Goal:** Confirm the GitLab→GitHub push mirror replicates tags and that GitHub Actions is enabled on the mirror. If either fails, the workflow can never fire — stop here.

**Files:** None (read-only verification).

- [ ] **Step 1: Verify the mirror pushed the most recent tag**

Run from repo root:

```bash
local_sha="$(git rev-parse v0.6.4)"
remote_line="$(git ls-remote https://github.com/aarontc/home-assistant-area-lighting.git refs/tags/v0.6.4)"
echo "local:  $local_sha"
echo "remote: $remote_line"
```

Expected: `remote_line` starts with the same SHA as `local_sha`, followed by `refs/tags/v0.6.4`.

If the remote returns nothing or a different SHA, the mirror is not pushing tags. **Stop.** Fix on GitLab: Settings → Repository → Mirroring repositories → confirm the GitHub mirror entry has "Mirror branches" set to "All branches" (which includes tags) and that the last update was successful. Re-run this step before continuing.

- [ ] **Step 2: Verify GitHub Actions is enabled on the mirror**

Open https://github.com/aarontc/home-assistant-area-lighting/actions in a browser.

Expected: a tab with either "There are no workflows in this repository yet" or a list of past runs. **Not** an "Actions disabled" banner.

If disabled, enable via Settings → Actions → General → "Allow all actions and reusable workflows".

- [ ] **Step 3: Verify the mirror push includes `.github/` files**

```bash
git ls-remote https://github.com/aarontc/home-assistant-area-lighting.git HEAD
```

Compare the SHA to `git rev-parse HEAD`. They should match (within a few seconds of any recent push to GitLab).

If they match, every commit's `.github/` directory reaches the mirror — confirming the workflow file (added in Task 3, pushed in Task 4) will arrive on GitHub. If the remote HEAD lags significantly, sync is broken.

---

## Task 2: Build and validate the release-notes bash locally

**Goal:** Iterate on the notes-generation bash against real tag history before embedding in YAML, where iteration would require committing + pushing per change. Use fixture-based validation (golden-file diff) for the four test cases identified in the spec.

**Files:**
- Create: `/tmp/build-release-notes.sh`
- Create: `/tmp/expected-v0.6.0.md`, `/tmp/expected-v0.6.1.md`, `/tmp/expected-v0.6.4.md`, `/tmp/expected-v0.5.0.md`

- [ ] **Step 1: Write the four expected-output fixtures**

Each fixture matches the spec's grouping rules: severity sections (Major/Minor/Patch/Other) with the prefix stripped, version-bump chore dropped, and a GitLab compare link.

```bash
cat > /tmp/expected-v0.6.0.md <<'EOF'
### Minor
- ci: bump manifest.json and pyproject.toml version on auto-tag

[Compare on GitLab](https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting/-/compare/v0.5.2...v0.6.0)
EOF

cat > /tmp/expected-v0.6.1.md <<'EOF'
### Patch
- docs: track HACS GitHub release requirement in TODO

[Compare on GitLab](https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting/-/compare/v0.6.0...v0.6.1)
EOF

cat > /tmp/expected-v0.6.4.md <<'EOF'
### Other
- fix: only reconcile startup state when persisted state exists
- fix: reconcile persisted OFF state with physical lights on startup

[Compare on GitLab](https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting/-/compare/v0.6.3...v0.6.4)
EOF

cat > /tmp/expected-v0.5.0.md <<'EOF'
### Minor
- area_lighting: add YAML reload service

[Compare on GitLab](https://gitlab.idleengineers.com/aaron/home-assistant-area-lighting/-/compare/v0.4.2...v0.5.0)
EOF
```

These were derived from `git log <prev>..<tag> --format='%h %s'`. If the local git history doesn't match (e.g., this plan is being run after additional history rewrites), regenerate them by hand from `git log` output.

- [ ] **Step 2: Write the script**

Create `/tmp/build-release-notes.sh` (writes to stdout — the workflow embeds the same logic but redirects to `/tmp/notes.md`):

```bash
#!/bin/bash
set -euo pipefail
TAG="${1:?tag required}"

prev="$(git describe --tags --abbrev=0 "${TAG}^" 2>/dev/null || true)"
range="${prev:+${prev}..}${TAG}"

subjects="$(git log "$range" --format='%s' \
  | grep -vE '^\(Patch\) release: bump version to ' || true)"

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
```

```bash
chmod +x /tmp/build-release-notes.sh
```

- [ ] **Step 3: Run the script against each fixture and diff**

Run from repo root (the script uses `git` against the current working directory):

```bash
for tag in v0.6.0 v0.6.1 v0.6.4 v0.5.0; do
  /tmp/build-release-notes.sh "$tag" > "/tmp/actual-${tag}.md"
  echo "=== diff $tag ==="
  diff -u "/tmp/expected-${tag}.md" "/tmp/actual-${tag}.md" && echo "OK"
done
```

Expected: four `OK` lines, no diff output between them. (Each `diff -u` exits 0 silently on match; the `&& echo OK` confirms.)

If any diff appears: the bash logic is wrong. Read the diff, identify whether it's a fixture error or a script bug, fix, re-run.

- [ ] **Step 4: Spot-check `v0.1.0` (no previous tag)**

This is the first-tag edge case — no previous tag exists, so `git describe --tags --abbrev=0 "v0.1.0^"` fails and `prev=""`. The script should produce notes from the full pre-v0.1.0 history with no compare link.

```bash
/tmp/build-release-notes.sh v0.1.0
```

Expected: severity-grouped output covering all commits up to v0.1.0, **no** `[Compare on GitLab]` line at the end. Hand-inspect — there's no fixture because the early history is long, but verify:
- The first tag of every section header line begins with `### ` (Major / Minor / Patch / Other).
- No line is a `(Patch) release: bump version to ...` chore (those started later).
- No trailing compare link.

If any of those fail, fix the script, re-run Step 3, then re-run this step.

- [ ] **Step 5: Commit point — script is validated, no source change yet**

Nothing to commit yet (the script lives in `/tmp/`). Do not commit `/tmp/build-release-notes.sh` — it's a development scratchpad, and the validated logic moves into the workflow YAML in Task 3.

---

## Task 3: Write the workflow file

**Goal:** Embed the validated bash from Task 2 into `.github/workflows/release.yaml`. No live test yet — just author the file, syntax-check, commit.

**Files:**
- Create: `.github/workflows/release.yaml`

- [ ] **Step 1: Create the directory and workflow file**

```bash
mkdir -p .github/workflows
```

Create `.github/workflows/release.yaml` with exactly this content:

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

- [ ] **Step 2: YAML syntax check**

Validate the file parses as YAML:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yaml'))" \
  && echo "YAML OK"
```

Expected: `YAML OK`. If a parse error appears, fix indentation/syntax and re-run.

- [ ] **Step 3: Sanity-check the bash blocks against the validated script**

Manually compare the `Build release notes` step's `run:` body to `/tmp/build-release-notes.sh`. They should be the same logic, with the workflow version wrapping the section-emitting code in `{ ... } > /tmp/notes.md` to redirect, and using `${TAG}` (env var set on the step) instead of `$1`.

```bash
diff <(sed -n '/^      - name: Build release notes/,/^      - name: Detect prerelease/p' .github/workflows/release.yaml) /tmp/build-release-notes.sh
```

This produces a noisy diff (different framings). What matters: the **inner bash logic** — the `git describe`, `git log`, `for sev`, `other=...`, and `if [ -n "$prev" ]` blocks — should match line-for-line. Eyeball the diff to confirm no logic drift.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yaml
git commit -m "$(cat <<'EOF'
(Patch) ci: GitHub Actions workflow to publish releases on tag push

Triggered by the GitLab→GitHub mirror's tag push. Generates
severity-grouped notes from git log, drops the version-bump chore
commit, and creates a GitHub release via the auto-provided
GITHUB_TOKEN. Eliminates the GitLab-CI poll-for-mirror approach.

Spec: docs/superpowers/specs/2026-04-17-github-release-publishing-design.md
EOF
)"
```

Expected: commit succeeds. Pre-commit hook (if active via `core.hooksPath=hooks`) accepts the `(Patch) ` prefix.

---

## Task 4: Push to GitLab and verify mirror sync to GitHub

**Goal:** Get the workflow file onto the GitHub mirror so `workflow_dispatch` becomes available.

**Files:** None.

- [ ] **Step 1: Push to GitLab**

```bash
git push origin main
```

Expected: push succeeds.

- [ ] **Step 2: Wait for mirror sync, then verify the workflow file landed on GitHub**

Mirror sync has been observed taking 5+ minutes in this repo. Verify with a generous deadline:

```bash
local_head="$(git rev-parse HEAD)"
for i in $(seq 1 60); do
  remote_head="$(git ls-remote https://github.com/aarontc/home-assistant-area-lighting.git HEAD | awk '{print $1}')"
  if [ "$local_head" = "$remote_head" ]; then
    echo "Mirror synced at attempt $i (${i}0s)"
    break
  fi
  echo "Attempt $i: local=$local_head remote=$remote_head — waiting 10s"
  sleep 10
done
```

Expected: "Mirror synced at attempt N" within ~10 minutes (worst case observed). If after 10 minutes the SHAs don't match, check GitLab Settings → Repository → Mirroring repositories for sync errors.

- [ ] **Step 3: Confirm the workflow file is visible on GitHub**

Open https://github.com/aarontc/home-assistant-area-lighting/blob/main/.github/workflows/release.yaml in a browser.

Expected: the file content matches what was committed locally.

- [ ] **Step 4: Confirm the workflow appears in the Actions tab**

Open https://github.com/aarontc/home-assistant-area-lighting/actions.

Expected: a workflow named "Publish GitHub release on tag" is listed in the left sidebar. Clicking it shows a "Run workflow" button (the `workflow_dispatch` trigger). If "Run workflow" is missing, the dispatch trigger isn't being picked up — re-check the YAML's `workflow_dispatch:` block.

---

## Task 5: Live verification — backfill v0.1.0

**Goal:** First live run, against the oldest tag (smallest blast radius if the formatting is wrong, and it's a real backfill of a tag with no release).

**Files:** None.

- [ ] **Step 1: Trigger the workflow for v0.1.0**

In the browser:
1. Go to https://github.com/aarontc/home-assistant-area-lighting/actions/workflows/release.yaml
2. Click "Run workflow" (top right)
3. Branch: `main` (default)
4. `Tag to (re)publish`: `v0.1.0`
5. `Overwrite notes if a release already exists`: leave unchecked
6. Click the green "Run workflow" button.

A new run appears in the runs list within a few seconds.

- [ ] **Step 2: Watch the run to completion**

Click the new run, then click the `publish` job. Expected: all five steps green within ~30 seconds:
1. Set up job
2. Resolve tag
3. Run actions/checkout@v4
4. Build release notes
5. Detect prerelease
6. Create or update release

If any step fails, expand it and read the log. Common failures:
- **Resolve tag returns empty:** `inputs.tag` not picked up — check the YAML.
- **Checkout fails with "couldn't find remote ref v0.1.0":** the mirror didn't push that tag. Verify with `git ls-remote https://github.com/aarontc/home-assistant-area-lighting.git refs/tags/v0.1.0`.
- **gh release create fails with 403:** `permissions: contents: write` missing or repo Actions permissions are read-only. Fix in repo Settings → Actions → General → "Workflow permissions" → "Read and write permissions".

- [ ] **Step 3: Inspect the resulting release**

Open https://github.com/aarontc/home-assistant-area-lighting/releases/tag/v0.1.0.

Expected:
- Title: `v0.1.0`.
- Body: severity-grouped sections for the pre-v0.1.0 history.
- **No** "Compare on GitLab" link (v0.1.0 has no predecessor tag).
- **Not** marked as Pre-release.

If the body looks wrong, do not proceed. Diagnose by re-running the local script in Task 2 against v0.1.0 and comparing to the published body.

---

## Task 6: Backfill remaining historical tags

**Goal:** Create GitHub releases for v0.2.0 through v0.8.1 so HACS shows the right version for users who installed older versions via release-pinned URLs. Zero releases exist today (the existing in-`tag:auto` poll has never produced one), so all 18 tags need backfilling — Task 5 covered v0.1.0; this task does the remaining 17.

**Files:** None.

- [ ] **Step 1: List the tags to backfill**

```bash
git tag --list 'v*' --sort=v:refname | grep -v '^v0\.1\.0$'
```

Expected: 17 tags (v0.2.0, v0.2.1, v0.3.0, v0.4.0, v0.4.1, v0.4.2, v0.5.0, v0.5.1, v0.5.2, v0.6.0, v0.6.1, v0.6.2, v0.6.3, v0.6.4, v0.7.0, v0.8.0, v0.8.1 — adjust if the count has drifted).

- [ ] **Step 2: Dispatch the workflow for each tag**

Use the GitHub web UI (the `gh` CLI isn't installed for this user). For each tag in the list above, repeat the procedure from Task 5 Step 1 (Actions → workflow → Run workflow → enter tag → Run). 17 dispatches, mechanical.

A separate browser tab on https://github.com/aarontc/home-assistant-area-lighting/actions makes it easy to dispatch one tag, switch tabs, see the run, then come back to dispatch the next.

- [ ] **Step 3: Wait for runs to complete and verify success**

In the browser, watch https://github.com/aarontc/home-assistant-area-lighting/actions until all dispatched runs are green (each takes ~30 seconds; runs are parallel because the concurrency group key includes the tag name, so one tag can't block another).

Expected: every dispatched run succeeds. Any failures: open the failing run, read the log, fix, redispatch with `replace=true` if a partial release was created.

- [ ] **Step 4: Spot-check three releases**

Open in the browser:
- https://github.com/aarontc/home-assistant-area-lighting/releases/tag/v0.6.0
- https://github.com/aarontc/home-assistant-area-lighting/releases/tag/v0.6.1
- https://github.com/aarontc/home-assistant-area-lighting/releases/tag/v0.6.4

Expected: each release body matches the corresponding `/tmp/expected-*.md` fixture from Task 2.

- [ ] **Step 5: Confirm the releases page shows the full set**

Open https://github.com/aarontc/home-assistant-area-lighting/releases.

Expected: 18 releases listed (v0.1.0 through v0.8.1). The most recent is at the top.

---

## Task 7: Remove the in-`tag:auto` GitHub release code

**Goal:** With the new workflow live and historical tags backfilled, strip the now-redundant GitHub release logic from GitLab CI and the Dagger module. After this task, the only path that creates GitHub releases is `.github/workflows/release.yaml`.

**Files:**
- Modify: `.gitlab-ci.yml` (`tag:auto` job header comment + flags on the `dagger call`)
- Modify: `dagger/version.go` (`CreateTag` signature + body, several helper functions, `time` import)
- Modify: `CONTRIBUTING.md` (drop "Publishing GitHub Releases" section + trailing flag note in "Tagging manually")

- [ ] **Step 1: Strip the GitHub flags from `.gitlab-ci.yml`**

In the `tag:auto` job header comment, replace the four-line `Required CI/CD variables:` block (including the `GITHUB_TOKEN` lines) and the trailing "The release step has to run in this same job" paragraph with a single-line variable note. After the edit, the comment block immediately above `tag:auto:` should read exactly:

```yaml
# Auto-tag pushes to the default branch. The severity is derived from commit
# subjects carrying `(Major)`, `(Minor)`, or `(Patch)` prefixes — see
# CONTRIBUTING.md#versioning. Requires the `PROJECT_ACCESS_TOKEN` CI/CD
# variable to be set to a token with `write_repository` scope.
```

In the `tag:auto.script` block, drop the `--github-token=env:GITHUB_TOKEN` and `--github-repo=aarontc/home-assistant-area-lighting` continuation lines. The remaining `dagger call` should read exactly:

```yaml
script:
  - |
    dagger call create-tag \
      --source=. \
      --gitlab-url=$CI_SERVER_URL \
      --project-id=$CI_PROJECT_ID \
      --token=env:PROJECT_ACCESS_TOKEN \
      --branch=$CI_COMMIT_BRANCH
```

- [ ] **Step 2: Strip the GitHub release code from `dagger/version.go`**

Three categories of edit:

**(a)** Remove the `"time"` import (line 12). After the edit, the import block reads:

```go
import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"

	"dagger/area-lighting/internal/dagger"
	"dagger/area-lighting/versioning"
)
```

**(b)** Replace the entire `CreateTag` function (current docstring + signature + body, ~89 lines) with the simpler form below — drops the `githubToken`/`githubRepo` parameters and the trailing `if githubToken != nil && githubRepo != ""` block:

```go
// CreateTag calculates the next version, commits updated version strings
// to manifest.json and pyproject.toml, then creates a Git tag on that
// commit via the GitLab API. `token` needs `write_repository` scope.
//
// The version-bump commit uses [skip ci] to prevent a feedback loop
// (the commit itself would otherwise trigger another tag:auto run).
// The same marker also suppresses the tag's own pipeline, so the
// `$CI_COMMIT_TAG` event never fires for this repo. Publishing the
// GitHub release is handled separately by the GitHub Actions workflow
// at `.github/workflows/release.yaml`, which is triggered when the
// GitLab → GitHub push mirror delivers the new tag.
func (m *AreaLighting) CreateTag(
	ctx context.Context,
	// +defaultPath="."
	source *dagger.Directory,
	// GitLab base URL, e.g. https://gitlab.idleengineers.com
	gitlabURL string,
	// Project ID or URL-encoded full path, e.g. "aaron/home-assistant-area-lighting"
	projectID string,
	// GitLab API token with write_repository scope
	token *dagger.Secret,
	// Branch to commit the version bump to (default: main)
	// +optional
	// +default="main"
	branch string,
) (string, error) {
	nextVersion, err := m.NextVersion(ctx, source)
	if err != nil {
		return "", fmt.Errorf("failed to calculate next version: %w", err)
	}

	tokenPlain, err := token.Plaintext(ctx)
	if err != nil {
		return "", fmt.Errorf("failed to read token: %w", err)
	}

	// Bump version files and commit via GitLab API.
	bumpSHA, err := createVersionBumpCommit(
		ctx, gitlabURL, projectID, tokenPlain, nextVersion, branch,
	)
	if err != nil {
		return "", fmt.Errorf("failed to bump version files: %w", err)
	}

	// Tag the version-bump commit (not the original HEAD).
	if err := createGitLabTag(ctx, gitlabURL, projectID, tokenPlain, nextVersion, bumpSHA); err != nil {
		return "", err
	}
	return fmt.Sprintf("Created tag %s (version bump commit %s)", nextVersion, bumpSHA[:8]), nil
}
```

**(c)** Delete five entire functions in their existing positions in the file:

- `CreateRelease` (the standalone backfill function — `func (m *AreaLighting) CreateRelease(...)`)
- `resolvePrevTag` (`func resolvePrevTag(...)`)
- `buildReleaseBody` (`func buildReleaseBody(...)`)
- `waitForGitHubTag` (`func waitForGitHubTag(...)`)
- `createGitHubRelease` (`func createGitHubRelease(...)`)

After this step, the only functions remaining in `dagger/version.go` are `NextVersion`, `CommitsSinceTag`, `CreateTag`, `TestVersioning`, `gitContainer`, `getCommitsSinceTag`, `createVersionBumpCommit`, `readGitLabFile`, `readBranchHead`, `createGitLabTag`. Verify with:

```bash
grep -E '^func ' dagger/version.go
```

Expected output (10 functions, in this order):
```
func (m *AreaLighting) NextVersion(
func (m *AreaLighting) CommitsSinceTag(
func (m *AreaLighting) CreateTag(
func (m *AreaLighting) TestVersioning(
func gitContainer(source *dagger.Directory) *dagger.Container {
func getCommitsSinceTag(ctx context.Context, git *dagger.Container, tag string) ([]string, error) {
func createVersionBumpCommit(
func readGitLabFile(
func readBranchHead(
func createGitLabTag(ctx context.Context, gitlabURL, projectID, token, tagName, ref string) error {
```

If `CreateRelease`, `resolvePrevTag`, `buildReleaseBody`, `waitForGitHubTag`, or `createGitHubRelease` still appear, repeat step (c) for the missing ones.

- [ ] **Step 3: Verify the Go file is well-formed**

```bash
gofmt -d dagger/version.go
```

Expected: no diff output (file is gofmt-clean). If diff appears, run `gofmt -w dagger/version.go` and recheck.

```bash
grep -n '"time"' dagger/version.go
```

Expected: no output (the import was removed). If the line still appears, remove it.

- [ ] **Step 4: (Optional) Verify with Dagger if available**

If the Dagger CLI is installed locally:

```bash
dagger call all
```

Expected: lint, typecheck, test, and TestVersioning all pass.

If Dagger isn't installed, skip — the next CI run on push will catch any regression. The Go syntax is the only thing this code change can break, and `gofmt -d` already validated that in Step 3.

- [ ] **Step 5: Update `CONTRIBUTING.md`**

Two deletions:

**(a)** Remove the entire "### Publishing GitHub Releases" section, currently around lines 114–146. The section starts at the heading `### Publishing GitHub Releases` and ends with the closing triple-backtick of the `dagger call create-release` example block (just before the next heading `### Tagging manually`). Delete the heading, all paragraphs, the bullet list of token requirements, and the example code block. Verify nothing remains:

```bash
grep -n 'Publishing GitHub Releases\|create-release\|GITHUB_TOKEN' CONTRIBUTING.md
```

Expected: no output.

**(b)** In the "### Tagging manually" section, delete the trailing two-line note that reads:

```
Pass `--github-token=env:GITHUB_TOKEN --github-repo=aarontc/home-assistant-area-lighting`
as well to also publish the GitHub release.
```

The "Tagging manually" section's last content should be the closing ` ``` ` of the `dagger call create-tag` example block. Verify:

```bash
grep -n 'github-token\|github-repo' CONTRIBUTING.md
```

Expected: no output.

- [ ] **Step 6: Commit the migration**

```bash
git add .gitlab-ci.yml dagger/version.go CONTRIBUTING.md
git commit -m "$(cat <<'EOF'
(Patch) ci: remove in-tag:auto GitHub release code

Replaced by .github/workflows/release.yaml, which is triggered when
the GitLab→GitHub mirror delivers the tag and creates the release
via the auto-provided GITHUB_TOKEN. tag:auto goes back to doing only
what it can do synchronously: bump version files and create the
GitLab tag.

Removes from dagger/version.go: githubToken/githubRepo parameters on
CreateTag, the post-tag release block, and the standalone
CreateRelease function plus its helpers (waitForGitHubTag,
createGitHubRelease, buildReleaseBody, resolvePrevTag). Drops the
unused time import.

The GITHUB_TOKEN GitLab CI/CD variable can now be removed via the
GitLab UI; left in place is harmless (nothing reads it).

Spec: docs/superpowers/specs/2026-04-17-github-release-publishing-design.md
EOF
)"
```

Expected: commit succeeds. Pre-commit hook accepts the `(Patch)` prefix.

- [ ] **Step 7: Push**

```bash
git push origin main
```

Expected: push succeeds. The next `tag:auto` run (next merge to `main`) will use the slimmed-down `dagger call create-tag`.

---

## Task 8: Update TODO.md

**Goal:** Replace the outdated "Done" entry for the HACS release item. The current entry references a `release:github` GitLab CI job that no longer exists (it was replaced by the in-`tag:auto` code, which itself was just removed in Task 7) and claims "Done" while zero releases existed at the time of writing — the real done-state is the workflow now in place.

**Files:**
- Modify: `TODO.md` (the "HACS version display" item — currently a single bullet at line 34, claiming the old `release:github` job)

- [ ] **Step 1: Replace the existing "Done" entry**

Edit `TODO.md`. The current single-line bullet for HACS version display reads (at line ~34):

```markdown
* ~~**HACS version display: create GitHub releases from tags.**~~ **Done.** `release:github` GitLab CI job (added 2026-04-17) calls the GitHub Releases API on every tag pipeline via the `create-release` Dagger function. Requires the `GITHUB_TOKEN` CI/CD variable (fine-grained PAT with `contents: write` on the mirror repo). Release body is built from commit subjects between the previous tag and the new tag.
```

Replace that bullet (only that bullet — leave other items intact) with:

```markdown
* ~~**HACS version display: create GitHub releases from tags.**~~ **Done.** GitHub Actions workflow `.github/workflows/release.yaml` triggers when the GitLab→GitHub push mirror delivers a `v*` tag, generates severity-grouped notes from `git log`, and creates the release using the auto-provided `GITHUB_TOKEN`. Tags v0.1.0 through v0.8.1 backfilled via `workflow_dispatch`; subsequent releases are automatic. Replaced an earlier in-`tag:auto` poll-and-release attempt that never produced a successful release. Spec at `docs/superpowers/specs/2026-04-17-github-release-publishing-design.md`.
```

- [ ] **Step 2: Commit**

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
(Patch) docs: update HACS GitHub-release TODO entry

Replace the outdated reference to the now-removed `release:github`
job with the current workflow-based design.

Spec: docs/superpowers/specs/2026-04-17-github-release-publishing-design.md
EOF
)"
```

- [ ] **Step 3: Push**

```bash
git push origin main
```

---

## Task 9: Clean up local scratch files

**Goal:** Remove the `/tmp/` artifacts from Task 2.

**Files:** `/tmp/build-release-notes.sh`, `/tmp/expected-*.md`, `/tmp/actual-*.md`.

- [ ] **Step 1: Remove the local scratch files**

```bash
rm -f /tmp/build-release-notes.sh /tmp/expected-v*.md /tmp/actual-v*.md
```

Expected: command succeeds silently.

---

## Final verification (post-implementation, no task)

The next time `tag:auto` cuts a real release on GitLab:

1. The version-bump commit is pushed to `main` on GitLab.
2. `tag:auto` creates the new `vX.Y.Z` tag on GitLab and exits (no
   longer waits for or talks to GitHub).
3. The GitLab→GitHub push mirror replicates the commit + tag. This
   has been observed taking 5+ minutes — slow, but no CI runner is
   held during the wait.
4. The `push: tags: ['v*']` trigger fires on GitHub.
5. The workflow runs (~30s).
6. A new GitHub release exists at https://github.com/aarontc/home-assistant-area-lighting/releases.
7. HACS picks it up on its next poll (typically within an hour) and shows the version in update notifications.

If steps 4–6 don't happen on the next release, check https://github.com/aarontc/home-assistant-area-lighting/actions for the run, expand any failures, and fix.

---

## Rollback

**To remove only the new workflow** (e.g., it misbehaves on the next live release):

```bash
git rm .github/workflows/release.yaml
git commit -m "(Patch) ci: revert GitHub release workflow"
git push origin main
```

The mirror will replicate the deletion to GitHub and the workflow stops triggering. Existing releases are not affected.

**To revert the migration** (restore the in-`tag:auto` GitHub release code) — separately or together with removing the workflow:

```bash
git revert <Task-7-migration-commit-SHA>
git push origin main
```

The previous behaviour (poll-and-release inside `tag:auto`) returns. Note: that path produced zero successful releases, so reverting it isn't a real recovery — just a way back to the prior, also-broken state. Prefer fixing the new workflow.

**To delete a malformed release manually:**

- Browser: Releases page → click the release → "Delete" (top right).

The underlying tag is preserved either way.
