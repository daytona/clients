// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

// newBuildContext returns a context directory containing src/file.txt and
// note.txt, alongside a sibling directory holding outside.txt and outside.conf.
func newBuildContext(t *testing.T) (contextDir string, siblingDir string) {
	t.Helper()

	root := t.TempDir()
	contextDir = filepath.Join(root, "context")
	siblingDir = filepath.Join(root, "sibling")

	if err := os.MkdirAll(filepath.Join(contextDir, "src"), 0o755); err != nil {
		t.Fatalf("failed to create context directory: %v", err)
	}
	if err := os.MkdirAll(siblingDir, 0o755); err != nil {
		t.Fatalf("failed to create sibling directory: %v", err)
	}

	files := map[string]string{
		filepath.Join(contextDir, "src", "file.txt"): "context file",
		filepath.Join(contextDir, "note.txt"):        "context note",
		filepath.Join(siblingDir, "outside.txt"):     "outside file",
		filepath.Join(siblingDir, "outside.conf"):    "outside config",
	}
	for path, content := range files {
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatalf("failed to write %s: %v", path, err)
		}
	}

	return contextDir, siblingDir
}

func TestParseDockerfileResolvesSourcesInsideContext(t *testing.T) {
	contextDir, _ := newBuildContext(t)

	tests := []struct {
		name       string
		dockerfile string
		want       []string
	}{
		{
			name:       "dot prefixed directory",
			dockerfile: "FROM alpine\nCOPY ./src /app/src\n",
			want:       []string{filepath.Join(contextDir, "src")},
		},
		{
			name:       "nested file",
			dockerfile: "FROM alpine\nCOPY src/file.txt /app/file.txt\n",
			want:       []string{filepath.Join(contextDir, "src", "file.txt")},
		},
		{
			name:       "glob inside the context",
			dockerfile: "FROM alpine\nCOPY *.txt /app/\n",
			want:       []string{filepath.Join(contextDir, "note.txt")},
		},
		{
			name:       "whole context",
			dockerfile: "FROM alpine\nCOPY . /app\n",
			want:       []string{contextDir},
		},
		{
			name:       "absolute path is read relative to the context root",
			dockerfile: "FROM alpine\nCOPY /src/file.txt /app/file.txt\n",
			want:       []string{filepath.Join(contextDir, "src", "file.txt")},
		},
		{
			name:       "absolute path behind a chown flag",
			dockerfile: "FROM alpine\nCOPY --chown=1:1 /note.txt /app/note.txt\n",
			want:       []string{filepath.Join(contextDir, "note.txt")},
		},
		{
			name:       "parent navigation is stripped",
			dockerfile: "FROM alpine\nCOPY ../note.txt /app/note.txt\n",
			want:       []string{filepath.Join(contextDir, "note.txt")},
		},
		{
			name:       "ADD parent navigation is stripped",
			dockerfile: "FROM alpine\nADD ../../src/file.txt /app/file.txt\n",
			want:       []string{filepath.Join(contextDir, "src", "file.txt")},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sources, err := parseDockerfileForSources(tt.dockerfile, contextDir)
			if err != nil {
				t.Fatalf("parseDockerfileForSources returned an error: %v", err)
			}
			if !slices.Equal(sources, tt.want) {
				t.Errorf("sources = %v, want %v", sources, tt.want)
			}
		})
	}
}

func TestParseDockerfileDoesNotReachOutsideContext(t *testing.T) {
	contextDir, siblingDir := newBuildContext(t)

	tests := []struct {
		name       string
		dockerfile string
	}{
		{
			name:       "absolute host path",
			dockerfile: "FROM alpine\nCOPY " + filepath.Join(siblingDir, "outside.txt") + " /app/outside.txt\n",
		},
		{
			name:       "relative path above the context",
			dockerfile: "FROM alpine\nCOPY ../sibling/outside.txt /app/outside.txt\n",
		},
		{
			name:       "glob above the context",
			dockerfile: "FROM alpine\nCOPY ../sibling/*.conf /app/\n",
		},
		{
			name:       "ADD with a relative path above the context",
			dockerfile: "FROM alpine\nADD ../sibling/outside.txt /app/outside.txt\n",
		},
		{
			name:       "absolute host path behind a chown flag",
			dockerfile: "FROM alpine\nCOPY --chown=1:1 " + filepath.Join(siblingDir, "outside.txt") + " /app/outside.txt\n",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sources, err := parseDockerfileForSources(tt.dockerfile, contextDir)
			if err != nil {
				t.Fatalf("parseDockerfileForSources returned an error: %v", err)
			}
			for _, source := range sources {
				rel, relErr := filepath.Rel(contextDir, source)
				if relErr != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
					t.Errorf("source %s resolves outside the build context %s", source, contextDir)
				}
			}
		})
	}
}

func TestParseDockerfileRejectsSymlinkedSourcesOutsideContext(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("creating symlinks requires elevated privileges on Windows")
	}

	contextDir, siblingDir := newBuildContext(t)

	links := map[string]string{
		filepath.Join(contextDir, "leak.txt"): filepath.Join(siblingDir, "outside.txt"),
		filepath.Join(contextDir, "leakdir"):  siblingDir,
	}
	for link, target := range links {
		if err := os.Symlink(target, link); err != nil {
			t.Fatalf("failed to create symlink %s: %v", link, err)
		}
	}

	tests := []struct {
		name       string
		dockerfile string
		wantInErr  string
	}{
		{
			name:       "symlinked file",
			dockerfile: "FROM alpine\nCOPY leak.txt /app/leak.txt\n",
			wantInErr:  "leak.txt",
		},
		{
			name:       "file through a symlinked directory",
			dockerfile: "FROM alpine\nCOPY leakdir/outside.txt /app/outside.txt\n",
			wantInErr:  "leakdir/outside.txt",
		},
		{
			name:       "glob through a symlinked directory",
			dockerfile: "FROM alpine\nCOPY leakdir/*.conf /app/\n",
			wantInErr:  "leakdir/*.conf",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			sources, err := parseDockerfileForSources(tt.dockerfile, contextDir)
			if err == nil {
				t.Fatalf("expected an error, got sources %v", sources)
			}
			if !strings.Contains(err.Error(), "forbidden path outside the build context") {
				t.Errorf("error = %q, want it to mention the build context", err)
			}
			if !strings.Contains(err.Error(), tt.wantInErr) {
				t.Errorf("error = %q, want it to name %q", err, tt.wantInErr)
			}
		})
	}
}

func TestParseDockerfileAllowsSymlinksInsideContext(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("creating symlinks requires elevated privileges on Windows")
	}

	contextDir, _ := newBuildContext(t)

	link := filepath.Join(contextDir, "alias.txt")
	if err := os.Symlink(filepath.Join(contextDir, "note.txt"), link); err != nil {
		t.Fatalf("failed to create symlink: %v", err)
	}

	sources, err := parseDockerfileForSources("FROM alpine\nCOPY alias.txt /app/alias.txt\n", contextDir)
	if err != nil {
		t.Fatalf("parseDockerfileForSources returned an error: %v", err)
	}
	if !slices.Equal(sources, []string{link}) {
		t.Errorf("sources = %v, want %v", sources, []string{link})
	}
}
