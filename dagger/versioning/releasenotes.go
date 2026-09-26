package versioning

import (
	"fmt"
	"regexp"
	"strings"
)

// bumpSubjectRe matches the exact subject `createVersionBumpCommit` emits.
// Anchored at both ends so a commit that merely mentions the phrase in its
// subject is not mistaken for the bump itself — the same reasoning as the
// `workflow:rules` filter in `.gitlab-ci.yml`, which must stay in sync.
var bumpSubjectRe = regexp.MustCompile(`^\(Patch\) release: bump version to \d+\.\d+\.\d+$`)

// IsVersionBumpSubject reports whether a commit subject is the automated
// version-bump commit. Those carry no user-facing change, so release notes
// leave them out.
func IsVersionBumpSubject(subject string) bool {
	return bumpSubjectRe.MatchString(strings.TrimSpace(subject))
}

// notesSection is one severity heading and the entries under it.
type notesSection struct {
	heading  string
	severity Severity
}

// Ordered most significant first, matching how a reader scans a changelog.
var notesSections = []notesSection{
	{"Major", SeverityMajor},
	{"Minor", SeverityMinor},
	{"Patch", SeverityPatch},
}

// ReleaseNotes renders GitHub release notes from the commit subjects in a
// tag's range.
//
// Subjects are grouped under `### Major` / `### Minor` / `### Patch` by their
// severity prefix, which is stripped from the rendered entry; anything
// without a recognised prefix collects under `### Other` at the end. Empty
// sections are omitted, and the automated version-bump commit is dropped.
//
// compareURL, when non-empty, is appended as a trailing link back to the
// GitLab compare view. Pass "" for the first release, which has nothing to
// compare against.
//
// The result is never empty: a tag whose only commit was the bump would
// otherwise render as a blank release body on GitHub.
func ReleaseNotes(subjects []string, compareURL string) string {
	grouped := make(map[Severity][]string, len(notesSections))
	var other []string

	for _, subject := range subjects {
		subject = strings.TrimSpace(subject)
		if subject == "" || IsVersionBumpSubject(subject) {
			continue
		}
		severity := ParseSeverityPrefix(subject)
		if severity == SeverityNone {
			other = append(other, subject)
			continue
		}
		grouped[severity] = append(grouped[severity], stripSeverityPrefix(subject))
	}

	var b strings.Builder
	for _, section := range notesSections {
		entries := grouped[section.severity]
		if len(entries) == 0 {
			continue
		}
		fmt.Fprintf(&b, "### %s\n", section.heading)
		for _, entry := range entries {
			fmt.Fprintf(&b, "- %s\n", entry)
		}
		b.WriteString("\n")
	}
	if len(other) > 0 {
		b.WriteString("### Other\n")
		for _, entry := range other {
			fmt.Fprintf(&b, "- %s\n", entry)
		}
		b.WriteString("\n")
	}

	if b.Len() == 0 {
		b.WriteString("No user-facing changes.\n\n")
	}
	if compareURL != "" {
		fmt.Fprintf(&b, "[Compare on GitLab](%s)\n", compareURL)
	}
	return b.String()
}

// stripSeverityPrefix removes the leading `(Major)` / `(Minor)` / `(Patch)`
// marker and the space after it, leaving the human-readable subject.
func stripSeverityPrefix(subject string) string {
	for _, prefix := range []string{"(Major)", "(Minor)", "(Patch)"} {
		if strings.HasPrefix(subject, prefix) {
			return strings.TrimSpace(strings.TrimPrefix(subject, prefix))
		}
	}
	return subject
}

// MissingReleaseTags returns the tags that have no corresponding published
// release, preserving the order of `tags`. Used both to decide what to
// publish and, in audit mode, to report what the primary path missed.
func MissingReleaseTags(tags []string, existing []string) []string {
	published := make(map[string]struct{}, len(existing))
	for _, tag := range existing {
		published[tag] = struct{}{}
	}

	var missing []string
	for _, tag := range tags {
		if _, ok := published[tag]; !ok {
			missing = append(missing, tag)
		}
	}
	return missing
}
