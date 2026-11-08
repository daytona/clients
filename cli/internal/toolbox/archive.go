// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"archive/tar"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

var archiveBeforeRegularOpen func(target string)

// archiveAfterParentPinned runs once a regular entry's parent directory is
// open, just before the file is created in it (a test seam for swap races).
var archiveAfterParentPinned func(target string)

type archiveExtractor interface {
	Close() error
	ExtractDirectory(relPath string, target string, mode os.FileMode) error
	OpenRegular(relPath string, target string, mode os.FileMode) (*os.File, error)
	ExtractHardLink(header *tar.Header, relPath string, target string) error
	ExtractSymlink(header *tar.Header, relPath string, target string) error
}

// ArchiveFilter decides which entries of a directory archive are kept.
// rel is slash-separated and relative to the archived directory.
type ArchiveFilter interface {
	// Keep reports whether the entry is archived and, for a directory that is
	// not, whether the walk still looks inside it (something below it may be
	// kept; its directory entry is then written before the first kept child).
	// parent is the state Keep returned for the entry's directory (nil for an
	// entry at the top); state is handed to the directory's own entries.
	Keep(rel string, isDir bool, parent any) (keep, descend bool, state any)
}

// ArchiveDirectory streams dir as a tar archive with every entry.
func ArchiveDirectory(dir string) (io.ReadCloser, error) {
	return ArchiveDirectoryFiltered(dir, nil)
}

// ArchiveDirectoryFiltered streams dir as a tar archive with the entries
// filter keeps (every entry when filter is nil).
func ArchiveDirectoryFiltered(dir string, filter ArchiveFilter) (io.ReadCloser, error) {
	info, err := os.Stat(dir)
	if err != nil {
		return nil, fmt.Errorf("stat %s: %w", dir, err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("%s is not a directory", dir)
	}
	pr, pw := io.Pipe()
	go func() {
		tw := tar.NewWriter(pw)
		err := WalkArchiveEntries(dir, filter, func(rel, current string, info os.FileInfo) error {
			link := ""
			if info.Mode()&os.ModeSymlink != 0 {
				// Archive the link itself (like tar does). Extraction refuses
				// absolute and parent-relative (`..`) targets, so fail here,
				// at the source, with the offending path — not on the other
				// side with a half-extracted tree.
				var err error
				link, err = os.Readlink(current)
				if err != nil {
					return err
				}
				if filepath.IsAbs(link) || hasParentPathComponent(link) {
					return fmt.Errorf("cp -r: symlink %s -> %s cannot be copied (absolute or parent-relative link target); replace it with a relative link inside the directory or exclude it", rel, link)
				}
			}
			header, err := tar.FileInfoHeader(info, link)
			if err != nil {
				return err
			}
			header.Name = rel
			if info.IsDir() {
				header.Name += "/"
			}
			if err := tw.WriteHeader(header); err != nil {
				return err
			}
			if info.IsDir() || link != "" {
				return nil
			}
			file, err := os.Open(current)
			if err != nil {
				return err
			}
			_, err = io.Copy(tw, file)
			closeErr := file.Close()
			if err == nil {
				err = closeErr
			}
			return err
		})
		closeErr := tw.Close()
		if err == nil {
			err = closeErr
		}
		_ = pw.CloseWithError(err)
	}()
	return pr, nil
}

// WalkArchiveEntries calls fn, in archive order, for every entry of dir the
// filter keeps (all of them when filter is nil): rel is the slash-separated
// archive name, current the local path. A directory that is not kept but
// holds kept entries is reported just before the first of them, so the
// archive never names a child without its parent. A directory that is not
// kept and that the filter does not descend into is never read, and a
// permission error on a directory that is not kept is ignored.
func WalkArchiveEntries(dir string, filter ArchiveFilter, fn func(rel, current string, info os.FileInfo) error) error {
	type openDir struct {
		rel, current string
		entry        fs.DirEntry
		state        any
		kept         bool
		reported     bool
	}
	var open []openDir // the directories on the path to the current entry
	return filepath.WalkDir(dir, func(current string, entry fs.DirEntry, walkErr error) error {
		rel, err := filepath.Rel(dir, current)
		if err != nil {
			return err
		}
		if rel == "." {
			return walkErr
		}
		rel = filepath.ToSlash(rel)
		// Drop the directories the walk has left.
		for len(open) > 0 {
			top := open[len(open)-1].rel
			if top == rel || strings.HasPrefix(rel, top+"/") {
				break
			}
			open = open[:len(open)-1]
		}
		if walkErr != nil {
			// A directory that could not be read (it was reported first).
			if len(open) > 0 && open[len(open)-1].rel == rel && !open[len(open)-1].kept && errors.Is(walkErr, fs.ErrPermission) {
				return nil
			}
			return walkErr
		}
		var parent any
		if len(open) > 0 {
			parent = open[len(open)-1].state
		}
		keep, descend, state := true, true, any(nil)
		if filter != nil {
			keep, descend, state = filter.Keep(rel, entry.IsDir(), parent)
		}
		if entry.IsDir() {
			if !keep && !descend {
				return filepath.SkipDir
			}
			open = append(open, openDir{rel: rel, current: current, entry: entry, state: state, kept: keep})
		}
		if !keep {
			return nil
		}
		last := len(open)
		if entry.IsDir() {
			last-- // the entry itself is reported below
		}
		for i := 0; i < last; i++ {
			if open[i].reported {
				continue
			}
			info, err := open[i].entry.Info()
			if err != nil {
				return err
			}
			if err := fn(open[i].rel, open[i].current, info); err != nil {
				return err
			}
			open[i].reported = true
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if entry.IsDir() {
			open[len(open)-1].reported = true
		}
		return fn(rel, current, info)
	})
}

func ExtractArchive(r io.Reader, dst string) error {
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return fmt.Errorf("create %s: %w", dst, err)
	}
	extractor, err := newArchiveExtractor(dst)
	if err != nil {
		return err
	}
	defer extractor.Close()
	root, err := filepath.Abs(dst)
	if err != nil {
		return fmt.Errorf("resolve destination %s: %w", dst, err)
	}
	root = filepath.Clean(root)
	tr := tar.NewReader(r)
	for {
		header, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return fmt.Errorf("read tar stream: %w", err)
		}
		// The zip-slip barrier CodeQL (go/zipslip) recognises: any ".." in
		// the entry name is refused outright — including inside a component
		// such as `report..json`, a name we accept losing rather than carry
		// a static-analysis exception; `resolveArchiveTarget` re-checks
		// containment on the cleaned absolute target below.
		if strings.Contains(header.Name, "..") {
			return fmt.Errorf("tar entry escapes destination ('..' in name): %s", header.Name)
		}
		entry, err := resolveArchiveTarget(dst, header.Name)
		if err != nil {
			return err
		}
		if entry.target != root && !strings.HasPrefix(entry.target, dirPrefix(root)) {
			return fmt.Errorf("tar entry escapes destination: %s", header.Name)
		}
		// A root-mapped entry (".", "/", "") may not restyle the caller's
		// directory: skip its mode, keep extracting its children.
		if entry.relPath == "." || entry.relPath == "" {
			if header.Typeflag == tar.TypeDir {
				continue
			}
			return fmt.Errorf("tar entry %q maps to the destination root", header.Name)
		}
		switch header.Typeflag {
		case tar.TypeLink:
			if err := extractor.ExtractHardLink(header, entry.relPath, entry.target); err != nil {
				return err
			}
		case tar.TypeSymlink:
			if err := extractor.ExtractSymlink(header, entry.relPath, entry.target); err != nil {
				return err
			}
		case tar.TypeDir:
			if err := extractor.ExtractDirectory(entry.relPath, entry.target, os.FileMode(header.Mode)); err != nil {
				return err
			}
		case tar.TypeReg, tar.TypeRegA:
			if archiveBeforeRegularOpen != nil {
				archiveBeforeRegularOpen(entry.target)
			}
			mode := os.FileMode(header.Mode)
			file, err := extractor.OpenRegular(entry.relPath, entry.target, mode)
			if err != nil {
				return fmt.Errorf("create %s: %w", entry.target, err)
			}
			if _, err := io.Copy(file, tr); err != nil {
				_ = file.Close()
				return fmt.Errorf("write %s: %w", entry.target, err)
			}
			if err := file.Chmod(mode); err != nil {
				_ = file.Close()
				return fmt.Errorf("chmod %s: %w", entry.target, err)
			}
			if err := file.Close(); err != nil {
				return fmt.Errorf("close %s: %w", entry.target, err)
			}
		default:
			return fmt.Errorf("unsupported tar entry %s (%d)", header.Name, header.Typeflag)
		}
	}
}
