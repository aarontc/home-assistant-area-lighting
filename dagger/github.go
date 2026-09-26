package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"dagger/area-lighting/internal/dagger"
	"dagger/area-lighting/versioning"
)

// githubAPI is the REST base. A constant rather than a parameter: this
// project mirrors to github.com specifically, and a configurable host would
// only invite pointing release credentials somewhere unintended.
const githubAPI = "https://api.github.com"

// mirrorWaitTimeout bounds how long PublishGithubReleases waits for the
// GitLab push mirror to carry a freshly created tag over to GitHub. The
// mirror normally syncs within about a minute; well past that means it is
// broken, and failing is more useful than hanging the pipeline.
const mirrorWaitTimeout = 10 * time.Minute

// mirrorPollInterval is how often the mirror wait re-checks for the tag.
const mirrorPollInterval = 15 * time.Second

// githubClient bounds every API call. http.DefaultClient has no timeout, so
// a hung connection would stall the job past mirrorWaitTimeout and only be
// caught by the pipeline's own timeout.
var githubClient = &http.Client{Timeout: 30 * time.Second}

// PublishGithubReleases publishes a GitHub release for every `v*` tag that
// does not already have one, and is the primary release path.
//
// GitHub is only in this project's release chain because HACS installs from
// it. Publishing used to be a GitHub Actions workflow triggered by the push
// mirror carrying a tag over, but that trigger is unreliable: it fired for
// v1.1.1 and silently did not for v1.2.0, leaving the tag on GitHub with no
// release and HACS users on the previous version with nothing to alert
// anyone. Driving it from here instead means the release is published by the
// same pipeline that created the tag, and a failure fails that pipeline
// where it will be seen.
//
// A release is only ever created for a tag that already exists on GitHub.
// That matters: GitHub's create-release endpoint will happily invent a
// missing tag from the default branch tip, which would silently point the
// release at the wrong commit if the mirror had not caught up yet. So the
// tag is waited for, and its SHA verified against the local repository,
// before anything is published.
//
// Set checkOnly to audit instead of publish: it reports the tags missing a
// release and fails if there are any, without creating them. That is the
// scheduled safety net for the case where this job itself never ran.
//
// `source` must include `.git` with full history and tags, so it takes its
// own default path rather than the module-level `Source`.
func (m *AreaLighting) PublishGithubReleases(
	ctx context.Context,
	// +defaultPath="."
	source *dagger.Directory,
	// GitHub repository as "owner/name", e.g. "aarontc/home-assistant-area-lighting"
	repo string,
	// GitHub token with `contents: write` on that repository. Optional only
	// so checkOnly can audit a public repository without credentials.
	// +optional
	token *dagger.Secret,
	// Base URL of the GitLab project, used for the compare link in the notes
	// +optional
	gitlabProjectURL string,
	// Report tags missing a release and fail, instead of publishing them
	// +optional
	// +default=false
	checkOnly bool,
) (string, error) {
	tokenPlain := ""
	if token != nil {
		plain, err := token.Plaintext(ctx)
		if err != nil {
			return "", fmt.Errorf("read GitHub token: %w", err)
		}
		tokenPlain = strings.TrimSpace(plain)
	}
	if tokenPlain == "" && !checkOnly {
		return "", fmt.Errorf("a GitHub token is required to publish releases")
	}

	git := gitContainer(source)

	localTags, err := listLocalTags(ctx, git)
	if err != nil {
		return "", err
	}
	if len(localTags) == 0 {
		return "No v* tags found; nothing to publish.", nil
	}

	publishedTags, err := listGithubReleaseTags(ctx, repo, tokenPlain)
	if err != nil {
		return "", err
	}

	missing := versioning.MissingReleaseTags(localTags, publishedTags)
	if len(missing) == 0 {
		return fmt.Sprintf("All %d tags already have a GitHub release.", len(localTags)), nil
	}

	if checkOnly {
		return "", fmt.Errorf(
			"%d tag(s) missing a GitHub release: %s",
			len(missing), strings.Join(missing, ", "),
		)
	}

	// Publish newest first, and keep going after a failure. The newest tag is
	// the one HACS users are waiting on, so an old tag that cannot be
	// published (its commit missing from the mirror, say) must not stand in
	// front of it. Every failure is still reported, and the call still fails.
	var log strings.Builder
	var failures []string
	for i := len(missing) - 1; i >= 0; i-- {
		tag := missing[i]
		if err := publishOneRelease(ctx, git, repo, tokenPlain, tag, gitlabProjectURL, &log); err != nil {
			fmt.Fprintf(&log, "FAILED %s: %v\n", tag, err)
			failures = append(failures, tag)
		}
	}
	if len(failures) > 0 {
		return log.String(), fmt.Errorf(
			"failed to publish %d of %d release(s): %s",
			len(failures), len(missing), strings.Join(failures, ", "),
		)
	}
	return log.String(), nil
}

// publishOneRelease waits for a single tag to reach GitHub, verifies it
// points where the local repository says it should, renders its notes, and
// creates the release.
func publishOneRelease(
	ctx context.Context,
	git *dagger.Container,
	repo, token, tag, gitlabProjectURL string,
	log *strings.Builder,
) error {
	localSHA, err := revParse(ctx, git, tag+"^{commit}")
	if err != nil {
		return fmt.Errorf("resolve %s locally: %w", tag, err)
	}

	remoteSHA, err := waitForGithubTag(ctx, repo, token, tag)
	if err != nil {
		return err
	}
	if remoteSHA != localSHA {
		return fmt.Errorf(
			"%s points at %s on GitHub but %s locally; refusing to publish a release "+
				"for a tag that disagrees with the source of truth",
			tag, short(remoteSHA), short(localSHA),
		)
	}

	subjects, err := subjectsForTag(ctx, git, tag)
	if err != nil {
		return err
	}
	notes := versioning.ReleaseNotes(subjects, compareURL(ctx, git, gitlabProjectURL, tag))

	if err := createGithubRelease(ctx, repo, token, tag, notes); err != nil {
		return err
	}
	fmt.Fprintf(log, "Published %s (%s)\n", tag, short(localSHA))
	return nil
}

// waitForGithubTag blocks until the tag exists on GitHub, returning the
// commit SHA it resolves to. Annotated tags are dereferenced, so the result
// is always a commit rather than a tag object.
func waitForGithubTag(ctx context.Context, repo, token, tag string) (string, error) {
	deadline := time.Now().Add(mirrorWaitTimeout)
	for {
		sha, found, err := githubTagCommit(ctx, repo, token, tag)
		if err != nil {
			return "", err
		}
		if found {
			return sha, nil
		}
		if time.Now().After(deadline) {
			return "", fmt.Errorf(
				"tag %s has not reached GitHub after %s; the push mirror may be broken",
				tag, mirrorWaitTimeout,
			)
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(mirrorPollInterval):
		}
	}
}

// githubTagCommit resolves a tag to its commit SHA, following an annotated
// tag object to the commit it wraps.
func githubTagCommit(ctx context.Context, repo, token, tag string) (string, bool, error) {
	var ref struct {
		Object struct {
			Type string `json:"type"`
			SHA  string `json:"sha"`
		} `json:"object"`
	}
	status, err := githubGet(ctx, repo, token, "/git/ref/tags/"+tag, &ref)
	if err != nil {
		return "", false, err
	}
	if status == http.StatusNotFound {
		return "", false, nil
	}
	if status != http.StatusOK {
		return "", false, fmt.Errorf("GitHub ref API returned %d for %s", status, tag)
	}
	if ref.Object.Type != "tag" {
		return ref.Object.SHA, true, nil
	}

	var obj struct {
		Object struct {
			SHA string `json:"sha"`
		} `json:"object"`
	}
	status, err = githubGet(ctx, repo, token, "/git/tags/"+ref.Object.SHA, &obj)
	if err != nil {
		return "", false, err
	}
	if status != http.StatusOK {
		return "", false, fmt.Errorf("GitHub tag-object API returned %d for %s", status, tag)
	}
	return obj.Object.SHA, true, nil
}

// listGithubReleaseTags returns the tag names of every published release,
// following pagination.
//
// Drafts are skipped. An authenticated listing includes them, and a draft is
// invisible to HACS, so counting one as published would suppress the real
// release and let the audit pass over a gap users can actually see.
func listGithubReleaseTags(ctx context.Context, repo, token string) ([]string, error) {
	var tags []string
	for page := 1; ; page++ {
		var releases []struct {
			TagName string `json:"tag_name"`
			Draft   bool   `json:"draft"`
		}
		path := fmt.Sprintf("/releases?per_page=100&page=%d", page)
		status, err := githubGet(ctx, repo, token, path, &releases)
		if err != nil {
			return nil, err
		}
		if status != http.StatusOK {
			return nil, fmt.Errorf("GitHub releases API returned %d", status)
		}
		if len(releases) == 0 {
			return tags, nil
		}
		for _, r := range releases {
			if r.Draft {
				continue
			}
			tags = append(tags, r.TagName)
		}
	}
}

// createGithubRelease publishes one release. Prereleases are marked as such
// so HACS does not offer a `-rc` tag as a stable version.
func createGithubRelease(ctx context.Context, repo, token, tag, notes string) error {
	payload := map[string]any{
		"tag_name":   tag,
		"name":       tag,
		"body":       notes,
		"prerelease": strings.Contains(tag, "-"),
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal release payload: %w", err)
	}

	url := fmt.Sprintf("%s/repos/%s/releases", githubAPI, repo)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	setGithubHeaders(req, token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := githubClient.Do(req)
	if err != nil {
		return fmt.Errorf("call GitHub releases API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusCreated {
		return nil
	}

	respBody, _ := io.ReadAll(resp.Body)
	// Two pipelines running close together can both see a tag as missing and
	// both try to publish it; the loser gets 422 already_exists. The end state
	// is the one we wanted, so treat it as success rather than reddening a
	// main pipeline over a harmless race.
	if resp.StatusCode == http.StatusUnprocessableEntity &&
		strings.Contains(string(respBody), "already_exists") {
		return nil
	}
	return fmt.Errorf("GitHub releases API returned %d for %s: %s", resp.StatusCode, tag, string(respBody))
}

// githubGet issues an authenticated GET and decodes the body into out when
// the response is a 200. Returns the status so callers can treat 404 as a
// legitimate "not there yet" rather than an error.
func githubGet(ctx context.Context, repo, token, path string, out any) (int, error) {
	url := fmt.Sprintf("%s/repos/%s%s", githubAPI, repo, path)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, fmt.Errorf("create request: %w", err)
	}
	setGithubHeaders(req, token)

	resp, err := githubClient.Do(req)
	if err != nil {
		return 0, fmt.Errorf("call GitHub API %s: %w", path, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// Drain so the connection can be reused.
		_, _ = io.Copy(io.Discard, resp.Body)
		return resp.StatusCode, nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return resp.StatusCode, fmt.Errorf("decode GitHub response for %s: %w", path, err)
	}
	return resp.StatusCode, nil
}

func setGithubHeaders(req *http.Request, token string) {
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
}

// -----------------------------------------------------------------------------
// Local git helpers.
// -----------------------------------------------------------------------------

// listLocalTags returns every `v*` tag in ascending version order.
func listLocalTags(ctx context.Context, git *dagger.Container) ([]string, error) {
	out, err := git.
		WithExec([]string{"git", "tag", "--list", "v*", "--sort=v:refname"}).
		Stdout(ctx)
	if err != nil {
		return nil, fmt.Errorf("list tags: %w", err)
	}
	return nonEmptyLines(out), nil
}

// subjectsForTag returns the commit subjects a tag introduced, i.e. those
// between the preceding tag and this one. For the first tag, that is the
// whole history up to it.
//
// Merge commits are excluded: since releases land via merge requests, every
// release would otherwise carry a "Merge branch ... into 'main'" line, which
// is not a user-facing change and only pads the notes.
func subjectsForTag(ctx context.Context, git *dagger.Container, tag string) ([]string, error) {
	rng := tag
	if prev, err := previousTag(ctx, git, tag); err == nil && prev != "" {
		rng = prev + ".." + tag
	}
	out, err := git.
		WithExec([]string{"git", "log", "--no-merges", rng, "--format=%s"}).
		Stdout(ctx)
	if err != nil {
		return nil, fmt.Errorf("log %s: %w", rng, err)
	}
	return nonEmptyLines(out), nil
}

// previousTag returns the tag immediately preceding the given one, or "" when
// it is the first. Version order is used rather than `git describe --abbrev=0`
// so the range is defined by the release sequence itself: every tag here sits
// on the default branch's chain of version-bump commits, and picking the
// neighbour by version keeps the notes aligned with what shipped between two
// releases regardless of how the branches underneath were merged.
func previousTag(ctx context.Context, git *dagger.Container, tag string) (string, error) {
	tags, err := listLocalTags(ctx, git)
	if err != nil {
		return "", err
	}
	for i, t := range tags {
		if t == tag {
			if i == 0 {
				return "", nil
			}
			return tags[i-1], nil
		}
	}
	return "", nil
}

// compareURL builds the GitLab compare link for a tag's range, or "" when
// there is no preceding tag or no project URL configured.
func compareURL(ctx context.Context, git *dagger.Container, gitlabProjectURL, tag string) string {
	if gitlabProjectURL == "" {
		return ""
	}
	prev, err := previousTag(ctx, git, tag)
	if err != nil || prev == "" {
		return ""
	}
	return fmt.Sprintf("%s/-/compare/%s...%s", strings.TrimSuffix(gitlabProjectURL, "/"), prev, tag)
}

func revParse(ctx context.Context, git *dagger.Container, rev string) (string, error) {
	out, err := git.WithExec([]string{"git", "rev-parse", rev}).Stdout(ctx)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(out), nil
}

func nonEmptyLines(s string) []string {
	var lines []string
	for _, line := range strings.Split(strings.TrimSpace(s), "\n") {
		if line = strings.TrimSpace(line); line != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

func short(sha string) string {
	if len(sha) > 8 {
		return sha[:8]
	}
	return sha
}
