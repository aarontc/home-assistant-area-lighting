package main

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

// NextVersion calculates the next semantic version based on commits since
// the last tag. Commits are classified by their subject-line prefix:
// `(Major)`, `(Minor)`, or `(Patch)`; the highest severity wins. If no
// commit carries a recognised prefix, patch is assumed.
//
// `source` must include `.git`, so it takes its own default path (the
// module-level `Source` has `.git` ignored for faster uploads).
func (m *AreaLighting) NextVersion(
	ctx context.Context,
	// +defaultPath="."
	source *dagger.Directory,
) (string, error) {
	git := gitContainer(source)

	lastTag, err := git.
		WithExec([]string{"git", "describe", "--tags", "--abbrev=0"}).
		Stdout(ctx)
	if err != nil {
		lastTag = "v0.0.0"
	}
	lastTag = strings.TrimSpace(lastTag)

	commits, err := getCommitsSinceTag(ctx, git, lastTag)
	if err != nil {
		return "", err
	}
	if len(commits) == 0 {
		return "", fmt.Errorf("no commits since %s", lastTag)
	}

	major, minor, patch, err := versioning.ParseVersion(lastTag)
	if err != nil {
		return "", fmt.Errorf("failed to parse version %q: %w", lastTag, err)
	}

	severity := versioning.HighestSeverity(commits)
	if severity == versioning.SeverityNone {
		severity = versioning.SeverityPatch
	}
	major, minor, patch = versioning.IncrementVersion(major, minor, patch, severity)

	return fmt.Sprintf("v%d.%d.%d", major, minor, patch), nil
}

// CommitsSinceTag returns a human-readable list of commits since the last
// tag, annotated with the severity each would contribute to NextVersion.
// Useful for previewing what `NextVersion` will produce.
func (m *AreaLighting) CommitsSinceTag(
	ctx context.Context,
	// +defaultPath="."
	source *dagger.Directory,
) (string, error) {
	git := gitContainer(source)

	lastTag, err := git.
		WithExec([]string{"git", "describe", "--tags", "--abbrev=0"}).
		Stdout(ctx)
	if err != nil {
		lastTag = "v0.0.0"
	}
	lastTag = strings.TrimSpace(lastTag)

	commits, err := getCommitsSinceTag(ctx, git, lastTag)
	if err != nil {
		return "", err
	}
	if len(commits) == 0 {
		return fmt.Sprintf("No commits since %s", lastTag), nil
	}

	var result strings.Builder
	fmt.Fprintf(&result, "Commits since %s:\n", lastTag)
	for _, commit := range commits {
		label := "none"
		switch versioning.ParseSeverityPrefix(commit) {
		case versioning.SeverityMajor:
			label = "Major"
		case versioning.SeverityMinor:
			label = "Minor"
		case versioning.SeverityPatch:
			label = "Patch"
		}
		fmt.Fprintf(&result, "  [%s] %s\n", label, commit)
	}
	return result.String(), nil
}

// CreateTag calculates the next version, commits updated version strings
// to manifest.json and pyproject.toml, then creates a Git tag on that
// commit via the GitLab API. `token` needs `write_repository` scope.
//
// The version-bump commit and the tag ref both need to exist on the
// default branch without triggering another `tag:auto` run (which would
// loop). Suppression lives in `.gitlab-ci.yml`'s `workflow:rules`:
// bump-commit titles matching `^(Patch) release: bump version to ` and
// any tag push are filtered out before a pipeline is even created —
// keeping those events out of the pipeline history so the project
// status badge stays accurate.
//
// Publishing the GitHub release is handled separately by the GitHub
// Actions workflow at `.github/workflows/release.yaml`, which scans for
// tags missing a release every 5 minutes.
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

// -----------------------------------------------------------------------------
// Container / HTTP helpers.
// -----------------------------------------------------------------------------

// TestVersioning runs `go test` on the versioning subpackage inside a Go
// container. Exists so CI can validate the pure helpers without needing
// a Go toolchain on the host.
func (m *AreaLighting) TestVersioning(
	ctx context.Context,
	// +defaultPath="./dagger/versioning"
	source *dagger.Directory,
) (string, error) {
	return dag.Container().
		From("golang:1.25").
		WithMountedCache("/go/pkg/mod", dag.CacheVolume("go-mod-versioning")).
		WithMountedCache("/root/.cache/go-build", dag.CacheVolume("go-build-versioning")).
		WithMountedDirectory("/src", source).
		WithWorkdir("/src").
		WithExec([]string{"go", "mod", "init", "versioning"}).
		WithExec([]string{"go", "test", "-v", "./..."}).
		Stdout(ctx)
}

func gitContainer(source *dagger.Directory) *dagger.Container {
	return dag.Container().
		From("alpine/git:latest").
		WithMountedDirectory("/src", source).
		WithWorkdir("/src")
}

func getCommitsSinceTag(ctx context.Context, git *dagger.Container, tag string) ([]string, error) {
	var (
		output string
		err    error
	)
	if tag == "v0.0.0" {
		output, err = git.
			WithExec([]string{"git", "log", "--format=%s"}).
			Stdout(ctx)
	} else {
		output, err = git.
			WithExec([]string{"git", "log", fmt.Sprintf("%s..HEAD", tag), "--format=%s"}).
			Stdout(ctx)
	}
	if err != nil {
		return nil, fmt.Errorf("failed to get commits: %w", err)
	}

	output = strings.TrimSpace(output)
	if output == "" {
		return nil, nil
	}

	var commits []string
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			commits = append(commits, line)
		}
	}
	return commits, nil
}

// Version files to bump. The regex matches the version string in each
// file; the replacement uses the bare version (no "v" prefix).
var versionFiles = []struct {
	path    string
	pattern *regexp.Regexp
	format  string // fmt format string; receives the bare version
}{
	{
		path:    "custom_components/area_lighting/manifest.json",
		pattern: regexp.MustCompile(`"version"\s*:\s*"[^"]*"`),
		format:  `"version": "%s"`,
	},
	{
		path:    "pyproject.toml",
		pattern: regexp.MustCompile(`(?m)^version\s*=\s*"[^"]*"`),
		format:  `version = "%s"`,
	},
}

// createVersionBumpCommit reads the version files from the repo via the
// GitLab API, replaces the version string, and commits the result.
// Returns the new commit SHA. The commit's title format is load-bearing:
// `.gitlab-ci.yml`'s `workflow:rules` filters pipelines by matching
// `^(Patch) release: bump version to ` on the commit title, so the
// push doesn't trigger another `tag:auto` run.
func createVersionBumpCommit(
	ctx context.Context,
	gitlabURL, projectID, token, version, branch string,
) (string, error) {
	encodedProject := strings.ReplaceAll(projectID, "/", "%2F")
	bareVersion := strings.TrimPrefix(version, "v")

	type action struct {
		Action   string `json:"action"`
		FilePath string `json:"file_path"`
		Content  string `json:"content"`
	}

	var actions []action
	for _, vf := range versionFiles {
		content, err := readGitLabFile(ctx, gitlabURL, encodedProject, token, vf.path, branch)
		if err != nil {
			return "", fmt.Errorf("read %s: %w", vf.path, err)
		}
		updated := vf.pattern.ReplaceAllString(content, fmt.Sprintf(vf.format, bareVersion))
		if updated == content {
			continue // no change needed
		}
		actions = append(actions, action{
			Action:   "update",
			FilePath: vf.path,
			Content:  updated,
		})
	}

	if len(actions) == 0 {
		// Nothing to bump — return HEAD of branch.
		return readBranchHead(ctx, gitlabURL, encodedProject, token, branch)
	}

	payload := map[string]any{
		"branch":         branch,
		"commit_message": fmt.Sprintf("(Patch) release: bump version to %s", bareVersion),
		"actions":        actions,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("marshal commit payload: %w", err)
	}

	apiURL := fmt.Sprintf("%s/api/v4/projects/%s/repository/commits", gitlabURL, encodedProject)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("PRIVATE-TOKEN", token)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("call GitLab commits API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		respBody, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("GitLab commits API returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		ID string `json:"id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("decode commit response: %w", err)
	}
	return result.ID, nil
}

// readGitLabFile fetches a file's raw content from the GitLab repository
// files API.
func readGitLabFile(
	ctx context.Context,
	gitlabURL, encodedProject, token, filePath, ref string,
) (string, error) {
	encodedPath := strings.ReplaceAll(filePath, "/", "%2F")
	apiURL := fmt.Sprintf(
		"%s/api/v4/projects/%s/repository/files/%s/raw?ref=%s",
		gitlabURL, encodedProject, encodedPath, ref,
	)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("PRIVATE-TOKEN", token)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("GitLab files API returned %d: %s", resp.StatusCode, string(respBody))
	}

	content, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(content), nil
}

// readBranchHead returns the HEAD commit SHA for a branch via the
// GitLab branches API.
func readBranchHead(
	ctx context.Context,
	gitlabURL, encodedProject, token, branch string,
) (string, error) {
	apiURL := fmt.Sprintf(
		"%s/api/v4/projects/%s/repository/branches/%s",
		gitlabURL, encodedProject, branch,
	)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("PRIVATE-TOKEN", token)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("GitLab branches API returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Commit struct {
			ID string `json:"id"`
		} `json:"commit"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", err
	}
	return result.Commit.ID, nil
}

func createGitLabTag(ctx context.Context, gitlabURL, projectID, token, tagName, ref string) error {
	encodedProjectID := strings.ReplaceAll(projectID, "/", "%2F")
	apiURL := fmt.Sprintf("%s/api/v4/projects/%s/repository/tags", gitlabURL, encodedProjectID)

	payload := map[string]string{
		"tag_name": tagName,
		"ref":      ref,
		"message":  fmt.Sprintf("Release %s", tagName),
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("PRIVATE-TOKEN", token)

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("failed to call GitLab API: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("GitLab API returned %d: %s", resp.StatusCode, string(respBody))
	}
	return nil
}
