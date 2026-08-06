package versioning

import (
	"strings"
	"testing"
)

func TestIsVersionBumpSubject(t *testing.T) {
	tests := []struct {
		name    string
		subject string
		want    bool
	}{
		{"canonical bump", "(Patch) release: bump version to 1.2.3", true},
		{"bump with leading whitespace", "  (Patch) release: bump version to 0.9.10", true},
		{"missing version", "(Patch) release: bump version to", false},
		{"partial version", "(Patch) release: bump version to 1.2", false},
		{"trailing text after version", "(Patch) release: bump version to 1.2.3 and more", false},
		{"wrong severity", "(Minor) release: bump version to 1.2.3", false},
		{"merely mentions the phrase", "(Patch) docs: explain release: bump version to 1.2.3", false},
		{"ordinary commit", "(Minor) area_lighting: add a thing", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsVersionBumpSubject(tt.subject); got != tt.want {
				t.Errorf("IsVersionBumpSubject(%q) = %v, want %v", tt.subject, got, tt.want)
			}
		})
	}
}

func TestReleaseNotesGroupsBySeverity(t *testing.T) {
	subjects := []string{
		"(Patch) area_lighting: fix a thing",
		"(Major) area_lighting: break a thing",
		"(Minor) area_lighting: add a thing",
	}
	got := ReleaseNotes(subjects, "")

	wantOrder := []string{"### Major", "### Minor", "### Patch"}
	last := -1
	for _, heading := range wantOrder {
		i := strings.Index(got, heading)
		if i < 0 {
			t.Fatalf("ReleaseNotes() missing %q:\n%s", heading, got)
		}
		if i < last {
			t.Errorf("ReleaseNotes() heading %q out of order:\n%s", heading, got)
		}
		last = i
	}
	for _, want := range []string{
		"- area_lighting: break a thing",
		"- area_lighting: add a thing",
		"- area_lighting: fix a thing",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("ReleaseNotes() missing entry %q:\n%s", want, got)
		}
	}
}

func TestReleaseNotesOmitsEmptySeverities(t *testing.T) {
	got := ReleaseNotes([]string{"(Patch) area_lighting: fix a thing"}, "")
	if strings.Contains(got, "### Major") || strings.Contains(got, "### Minor") {
		t.Errorf("ReleaseNotes() should omit severities with no entries:\n%s", got)
	}
}

func TestReleaseNotesDropsVersionBumpCommits(t *testing.T) {
	subjects := []string{
		"(Patch) release: bump version to 1.2.3",
		"(Patch) area_lighting: fix a thing",
	}
	got := ReleaseNotes(subjects, "")
	if strings.Contains(got, "bump version to") {
		t.Errorf("ReleaseNotes() should drop the bump commit:\n%s", got)
	}
	if !strings.Contains(got, "- area_lighting: fix a thing") {
		t.Errorf("ReleaseNotes() dropped a real entry:\n%s", got)
	}
}

func TestReleaseNotesCollectsUnprefixedSubjectsUnderOther(t *testing.T) {
	subjects := []string{
		"(Patch) area_lighting: fix a thing",
		"Merge branch 'scene-self-healing'",
	}
	got := ReleaseNotes(subjects, "")
	if !strings.Contains(got, "### Other") {
		t.Errorf("ReleaseNotes() missing Other section:\n%s", got)
	}
	if !strings.Contains(got, "- Merge branch 'scene-self-healing'") {
		t.Errorf("ReleaseNotes() missing unprefixed entry:\n%s", got)
	}
	if strings.Index(got, "### Patch") > strings.Index(got, "### Other") {
		t.Errorf("ReleaseNotes() Other must come last:\n%s", got)
	}
}

func TestReleaseNotesAppendsCompareLink(t *testing.T) {
	got := ReleaseNotes([]string{"(Patch) x"}, "https://example.test/compare/v1.0.0...v1.1.0")
	if !strings.Contains(got, "[Compare on GitLab](https://example.test/compare/v1.0.0...v1.1.0)") {
		t.Errorf("ReleaseNotes() missing compare link:\n%s", got)
	}
}

func TestReleaseNotesWithoutCompareLink(t *testing.T) {
	got := ReleaseNotes([]string{"(Patch) x"}, "")
	if strings.Contains(got, "Compare on GitLab") {
		t.Errorf("ReleaseNotes() should omit the link when no URL is given:\n%s", got)
	}
}

func TestReleaseNotesIsNeverEmpty(t *testing.T) {
	// A tag whose only commit is the bump would otherwise produce empty
	// notes, and GitHub renders that as a blank release body.
	got := ReleaseNotes([]string{"(Patch) release: bump version to 1.2.3"}, "")
	if strings.TrimSpace(got) == "" {
		t.Error("ReleaseNotes() must not be empty")
	}
}

func TestReleaseNotesSkipsBlankSubjects(t *testing.T) {
	got := ReleaseNotes([]string{"", "   ", "(Patch) real one"}, "")
	if strings.Contains(got, "- \n") || strings.Contains(got, "-  ") {
		t.Errorf("ReleaseNotes() emitted a blank entry:\n%s", got)
	}
	if !strings.Contains(got, "- real one") {
		t.Errorf("ReleaseNotes() missing the real entry:\n%s", got)
	}
}

func TestMissingReleaseTags(t *testing.T) {
	tests := []struct {
		name     string
		tags     []string
		existing []string
		want     []string
	}{
		{
			"none published yet",
			[]string{"v1.0.0", "v1.1.0"},
			nil,
			[]string{"v1.0.0", "v1.1.0"},
		},
		{
			"all published",
			[]string{"v1.0.0", "v1.1.0"},
			[]string{"v1.0.0", "v1.1.0"},
			nil,
		},
		{
			"newest missing",
			[]string{"v1.0.0", "v1.1.0", "v1.2.0"},
			[]string{"v1.0.0", "v1.1.0"},
			[]string{"v1.2.0"},
		},
		{
			"gap in the middle",
			[]string{"v1.0.0", "v1.1.0", "v1.2.0"},
			[]string{"v1.0.0", "v1.2.0"},
			[]string{"v1.1.0"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := MissingReleaseTags(tt.tags, tt.existing)
			if len(got) != len(tt.want) {
				t.Fatalf("MissingReleaseTags() = %v, want %v", got, tt.want)
			}
			for i := range got {
				if got[i] != tt.want[i] {
					t.Fatalf("MissingReleaseTags() = %v, want %v", got, tt.want)
				}
			}
		})
	}
}
