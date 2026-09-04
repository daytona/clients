// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package common

import (
	"os"
	"path/filepath"
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

func TestParseDockerfileRejectsSourcesOutsideContext(t *testing.T) {
	contextDir, siblingDir := newBuildContext(t)

	tests := []struct {
		name       string
		dockerfile string
		wantInErr  string
	}{
		{
			name:       "absolute path",
			dockerfile: "FROM alpine\nCOPY " + filepath.Join(siblingDir, "outside.txt") + " /app/outside.txt\n",
			wantInErr:  filepath.Join(siblingDir, "outside.txt"),
		},
		{
			name:       "relative path above the context",
			dockerfile: "FROM alpine\nCOPY ../sibling/outside.txt /app/outside.txt\n",
			wantInErr:  "../sibling/outside.txt",
		},
		{
			name:       "glob above the context",
			dockerfile: "FROM alpine\nCOPY ../sibling/*.conf /app/\n",
			wantInErr:  "../sibling/*.conf",
		},
		{
			name:       "ADD with a relative path above the context",
			dockerfile: "FROM alpine\nADD ../sibling/outside.txt /app/outside.txt\n",
			wantInErr:  "../sibling/outside.txt",
		},
		{
			name:       "absolute path behind a chown flag",
			dockerfile: "FROM alpine\nCOPY --chown=1:1 " + filepath.Join(siblingDir, "outside.txt") + " /app/outside.txt\n",
			wantInErr:  filepath.Join(siblingDir, "outside.txt"),
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

func TestParseDockerfileAcceptsSourcesInsideContext(t *testing.T) {
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
