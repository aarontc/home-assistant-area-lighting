// Package versioning contains pure helpers for semantic-version bumping
// from commit-message prefixes. Kept separate from the Dagger module so
// the helpers can be unit-tested without the Dagger session panicking on
// init.
package versioning

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// Severity classifies a commit's bump level from its subject-line prefix.
type Severity int

const (
	SeverityNone Severity = iota
	SeverityPatch
	SeverityMinor
	SeverityMajor
)

var semverRe = regexp.MustCompile(`^(\d+)\.(\d+)\.(\d+)`)

// ParseVersion extracts the major.minor.patch triple from a tag like
// "v1.2.3" or "1.2.3-beta". An empty string parses as 0.0.0.
func ParseVersion(version string) (major, minor, patch int, err error) {
	version = strings.TrimPrefix(version, "v")
	if version == "" {
		return 0, 0, 0, nil
	}
	matches := semverRe.FindStringSubmatch(version)
	if matches == nil {
		return 0, 0, 0, fmt.Errorf("invalid version format: %s", version)
	}
	major, _ = strconv.Atoi(matches[1])
	minor, _ = strconv.Atoi(matches[2])
	patch, _ = strconv.Atoi(matches[3])
	return major, minor, patch, nil
}

// ParseSeverityPrefix returns the severity implied by a commit subject's
// leading marker: `(Major)`, `(Minor)`, or `(Patch)`. Matching is
// case-sensitive and the marker must be at the start of the trimmed subject.
func ParseSeverityPrefix(message string) Severity {
	message = strings.TrimSpace(message)
	switch {
	case strings.HasPrefix(message, "(Major)"):
		return SeverityMajor
	case strings.HasPrefix(message, "(Minor)"):
		return SeverityMinor
	case strings.HasPrefix(message, "(Patch)"):
		return SeverityPatch
	default:
		return SeverityNone
	}
}

// HighestSeverity returns the largest Severity across the given commit
// subjects.
func HighestSeverity(commits []string) Severity {
	highest := SeverityNone
	for _, commit := range commits {
		if s := ParseSeverityPrefix(commit); s > highest {
			highest = s
		}
	}
	return highest
}

// IncrementVersion applies a Severity bump to a major.minor.patch triple.
func IncrementVersion(major, minor, patch int, severity Severity) (int, int, int) {
	switch severity {
	case SeverityMajor:
		return major + 1, 0, 0
	case SeverityMinor:
		return major, minor + 1, 0
	case SeverityPatch:
		return major, minor, patch + 1
	default:
		return major, minor, patch
	}
}

// VersionFile is a file that records the release version. The bump
// commit rewrites each one with the bare version (no "v" prefix).
type VersionFile struct {
	Path    string
	pattern *regexp.Regexp
	format  string // fmt format string; receives the bare version
}

// Apply returns content with this file's version set to version. It
// fails when the file has no version to rewrite, so a release never
// ships with one file left on the old version.
func (f VersionFile) Apply(content, version string) (string, error) {
	if !f.pattern.MatchString(content) {
		return "", fmt.Errorf("no version found in %s", f.Path)
	}
	return f.pattern.ReplaceAllString(content, fmt.Sprintf(f.format, version)), nil
}

// VersionFiles lists every file the release bump rewrites.
var VersionFiles = []VersionFile{
	{
		Path:    "custom_components/area_lighting/manifest.json",
		pattern: regexp.MustCompile(`"version"\s*:\s*"[^"]*"`),
		format:  `"version": "%s"`,
	},
	{
		Path:    "pyproject.toml",
		pattern: regexp.MustCompile(`(?m)^version\s*=\s*"[^"]*"`),
		format:  `version = "%s"`,
	},
	{
		// uv.lock records the project's own version in its package entry;
		// leaving it behind makes the lock disagree with pyproject.toml.
		Path:    "uv.lock",
		pattern: regexp.MustCompile(`(?m)^name\s*=\s*"area-lighting"[ \t]*\nversion\s*=\s*"[^"]*"`),
		format:  "name = \"area-lighting\"\nversion = \"%s\"",
	},
}
