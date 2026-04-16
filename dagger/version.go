package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
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

// CreateTag calculates the next version and creates a Git tag on the given
// commit via the GitLab API. `token` needs `write_repository` scope.
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
) (string, error) {
	nextVersion, err := m.NextVersion(ctx, source)
	if err != nil {
		return "", fmt.Errorf("failed to calculate next version: %w", err)
	}

	commitSHA, err := gitContainer(source).
		WithExec([]string{"git", "rev-parse", "HEAD"}).
		Stdout(ctx)
	if err != nil {
		return "", fmt.Errorf("failed to get commit SHA: %w", err)
	}
	commitSHA = strings.TrimSpace(commitSHA)

	tokenPlain, err := token.Plaintext(ctx)
	if err != nil {
		return "", fmt.Errorf("failed to read token: %w", err)
	}

	if err := createGitLabTag(ctx, gitlabURL, projectID, tokenPlain, nextVersion, commitSHA); err != nil {
		return "", err
	}
	return fmt.Sprintf("Created tag %s", nextVersion), nil
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
