// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"archive/tar"
	"fmt"
	"io"
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

func ArchiveDirectory(dir string) (io.ReadCloser, error) {
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
		err := filepath.Walk(dir, func(current string, info os.FileInfo, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			rel, err := filepath.Rel(dir, current)
			if err != nil {
				return err
			}
			if rel == "." {
				return nil
			}
			link := ""
			if info.Mode()&os.ModeSymlink != 0 {
				// Archive the link itself (like tar does). Extraction refuses
				// absolute and parent-relative (`..`) targets, so fail here,
				// at the source, with the offending path — not on the other
				// side with a half-extracted tree.
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
			header.Name = filepath.ToSlash(rel)
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
