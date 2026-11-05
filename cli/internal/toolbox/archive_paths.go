// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type archiveTarget struct {
	relPath string
	target  string
}

func resolveArchiveTarget(dst string, name string) (archiveTarget, error) {
	base, err := filepath.Abs(dst)
	if err != nil {
		return archiveTarget{}, fmt.Errorf("resolve destination %s: %w", dst, err)
	}
	if strings.ContainsRune(name, 0) {
		return archiveTarget{}, fmt.Errorf("tar entry name contains a NUL byte: %q", name)
	}
	if hasParentPathComponent(name) {
		return archiveTarget{}, fmt.Errorf("tar entry escapes destination: %s", name)
	}
	rooted := filepath.Clean("/" + filepath.FromSlash(name))
	target := filepath.Clean(filepath.Join(base, strings.TrimPrefix(rooted, string(filepath.Separator))))
	// Containment, stated the way static analysers (CodeQL go/zipslip)
	// recognise as the sanitizer: the cleaned target is the base itself or
	// lies under the base's directory prefix. `within` below is the same
	// check via Rel.
	if target != base && !strings.HasPrefix(target, dirPrefix(base)) {
		return archiveTarget{}, fmt.Errorf("tar entry escapes destination: %s", name)
	}
	if !within(base, target) {
		return archiveTarget{}, fmt.Errorf("tar entry escapes destination: %s", name)
	}
	rel, err := filepath.Rel(base, target)
	if err != nil {
		return archiveTarget{}, err
	}
	cur := base
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		if part == "." || part == "" {
			continue
		}
		cur = filepath.Join(cur, part)
		fi, statErr := os.Lstat(cur)
		if statErr != nil {
			if os.IsNotExist(statErr) {
				break
			}
			return archiveTarget{}, statErr
		}
		if fi.Mode()&os.ModeSymlink != 0 {
			return archiveTarget{}, fmt.Errorf("tar entry %s traverses a symlink at %s", name, cur)
		}
	}
	return archiveTarget{relPath: rel, target: target}, nil
}

func safeTarget(dst string, name string) (string, error) {
	entry, err := resolveArchiveTarget(dst, name)
	if err != nil {
		return "", err
	}
	return entry.target, nil
}

func splitArchiveRel(rel string) []string {
	if rel == "." {
		return nil
	}
	parts := strings.Split(rel, string(filepath.Separator))
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if part == "" || part == "." {
			continue
		}
		out = append(out, part)
	}
	return out
}

// dirPrefix returns dir with exactly one trailing separator. A filesystem
// root (`/`, `C:\`) already ends in one; appending another would give `//`,
// which no child path starts with.
func dirPrefix(dir string) string {
	if strings.HasSuffix(dir, string(filepath.Separator)) {
		return dir
	}
	return dir + string(filepath.Separator)
}

func within(root string, p string) bool {
	rel, err := filepath.Rel(root, p)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func hasParentPathComponent(path string) bool {
	for _, part := range strings.FieldsFunc(path, func(r rune) bool {
		return r == '/' || r == '\\'
	}) {
		if part == ".." {
			return true
		}
	}
	return false
}

func evalSymlinksLenient(p string) (string, error) {
	existing := p
	var rest []string
	for {
		if _, err := os.Lstat(existing); err == nil {
			break
		}
		parent := filepath.Dir(existing)
		if parent == existing {
			return "", fmt.Errorf("no existing prefix for %s", p)
		}
		rest = append([]string{filepath.Base(existing)}, rest...)
		existing = parent
	}
	resolved, err := filepath.EvalSymlinks(existing)
	if err != nil {
		return "", err
	}
	return filepath.Clean(filepath.Join(append([]string{resolved}, rest...)...)), nil
}
