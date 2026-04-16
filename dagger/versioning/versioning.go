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
