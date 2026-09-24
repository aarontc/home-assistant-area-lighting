package versioning

import "testing"

func TestParseVersion(t *testing.T) {
	tests := []struct {
		name                            string
		version                         string
		wantMajor, wantMinor, wantPatch int
		wantErr                         bool
	}{
		{"standard version with v prefix", "v1.2.3", 1, 2, 3, false},
		{"version without v prefix", "1.2.3", 1, 2, 3, false},
		{"zero version", "v0.0.0", 0, 0, 0, false},
		{"large numbers", "v10.20.30", 10, 20, 30, false},
		{"empty version", "", 0, 0, 0, false},
		{"version with suffix", "v1.2.3-beta", 1, 2, 3, false},
		{"invalid format", "invalid", 0, 0, 0, true},
		{"partial version", "v1.2", 0, 0, 0, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			major, minor, patch, err := ParseVersion(tt.version)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParseVersion() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr && (major != tt.wantMajor || minor != tt.wantMinor || patch != tt.wantPatch) {
				t.Errorf("ParseVersion() = %v.%v.%v, want %v.%v.%v",
					major, minor, patch, tt.wantMajor, tt.wantMinor, tt.wantPatch)
			}
		})
	}
}

func TestParseSeverityPrefix(t *testing.T) {
	tests := []struct {
		name    string
		message string
		want    Severity
	}{
		{"major prefix", "(Major) Breaking change", SeverityMajor},
		{"minor prefix", "(Minor) New feature", SeverityMinor},
		{"patch prefix", "(Patch) Bug fix", SeverityPatch},
		{"major with leading whitespace", "  (Major) Change", SeverityMajor},
		{"no prefix", "Some commit message", SeverityNone},
		{"lowercase prefix", "(major) This should not match", SeverityNone},
		{"prefix in middle", "Fix (Patch) something", SeverityNone},
		{"empty message", "", SeverityNone},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ParseSeverityPrefix(tt.message); got != tt.want {
				t.Errorf("ParseSeverityPrefix() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestHighestSeverity(t *testing.T) {
	tests := []struct {
		name    string
		commits []string
		want    Severity
	}{
		{"empty list", []string{}, SeverityNone},
		{"single major", []string{"(Major) Breaking change"}, SeverityMajor},
		{"single minor", []string{"(Minor) New feature"}, SeverityMinor},
		{"single patch", []string{"(Patch) Bug fix"}, SeverityPatch},
		{"mixed severities", []string{"(Patch) Fix", "(Minor) Add", "(Patch) Another"}, SeverityMinor},
		{"major wins", []string{"(Minor) Feature", "(Major) Break", "(Patch) Fix"}, SeverityMajor},
		{"no valid prefixes", []string{"Random commit", "Another random"}, SeverityNone},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := HighestSeverity(tt.commits); got != tt.want {
				t.Errorf("HighestSeverity() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestIncrementVersion(t *testing.T) {
	tests := []struct {
		name                            string
		major, minor, patch             int
		severity                        Severity
		wantMajor, wantMinor, wantPatch int
	}{
		{"increment major", 1, 2, 3, SeverityMajor, 2, 0, 0},
		{"increment minor", 1, 2, 3, SeverityMinor, 1, 3, 0},
		{"increment patch", 1, 2, 3, SeverityPatch, 1, 2, 4},
		{"no change for none", 1, 2, 3, SeverityNone, 1, 2, 3},
		{"major from zero", 0, 0, 0, SeverityMajor, 1, 0, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotMajor, gotMinor, gotPatch := IncrementVersion(tt.major, tt.minor, tt.patch, tt.severity)
			if gotMajor != tt.wantMajor || gotMinor != tt.wantMinor || gotPatch != tt.wantPatch {
				t.Errorf("IncrementVersion() = %v.%v.%v, want %v.%v.%v",
					gotMajor, gotMinor, gotPatch, tt.wantMajor, tt.wantMinor, tt.wantPatch)
			}
		})
	}
}

func TestVersionFilesApply(t *testing.T) {
	inputs := map[string]string{
		"custom_components/area_lighting/manifest.json": "{\n  \"domain\": \"area_lighting\",\n  \"version\": \"1.2.0\"\n}\n",
		"pyproject.toml": "[project]\nname = \"area-lighting\"\nversion = \"1.2.0\"\nrequires-python = \">=3.14.2\"\n",
		// Another package on the same version must keep it.
		"uv.lock": "[[package]]\nname = \"anyio\"\nversion = \"1.2.0\"\n\n[[package]]\nname = \"area-lighting\"\nversion = \"1.2.0\"\nsource = { virtual = \".\" }\n",
	}
	want := map[string]string{
		"custom_components/area_lighting/manifest.json": "{\n  \"domain\": \"area_lighting\",\n  \"version\": \"2.0.0\"\n}\n",
		"pyproject.toml": "[project]\nname = \"area-lighting\"\nversion = \"2.0.0\"\nrequires-python = \">=3.14.2\"\n",
		"uv.lock":        "[[package]]\nname = \"anyio\"\nversion = \"1.2.0\"\n\n[[package]]\nname = \"area-lighting\"\nversion = \"2.0.0\"\nsource = { virtual = \".\" }\n",
	}
	if len(VersionFiles) != len(inputs) {
		t.Fatalf("VersionFiles has %d entries, test covers %d", len(VersionFiles), len(inputs))
	}
	for _, f := range VersionFiles {
		t.Run(f.Path, func(t *testing.T) {
			got, err := f.Apply(inputs[f.Path], "2.0.0")
			if err != nil {
				t.Fatalf("Apply(%s): %v", f.Path, err)
			}
			if got != want[f.Path] {
				t.Errorf("Apply(%s) =\n%s\nwant\n%s", f.Path, got, want[f.Path])
			}
		})
	}
}

func TestVersionFilesApplyUvLockSpacing(t *testing.T) {
	lock := VersionFiles[len(VersionFiles)-1]
	got, err := lock.Apply("[[package]]\nname=\"area-lighting\"\nversion=\"1.2.0\"\n", "2.0.0")
	if err != nil {
		t.Fatal(err)
	}
	if want := "[[package]]\nname = \"area-lighting\"\nversion = \"2.0.0\"\n"; got != want {
		t.Errorf("Apply = %q, want %q", got, want)
	}
}

func TestVersionFilesApplyFailsWithoutAVersion(t *testing.T) {
	for _, f := range VersionFiles {
		t.Run(f.Path, func(t *testing.T) {
			if _, err := f.Apply("no version here\n", "2.0.0"); err == nil {
				t.Errorf("Apply(%s) on content without a version: want error", f.Path)
			}
		})
	}
}
