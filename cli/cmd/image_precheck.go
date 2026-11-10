// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"errors"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
)

// checkImageDockerfile checks directories and plain tar headers without uploading.
// Opaque archives (for example compressed tars) are checked by the server.
func checkImageDockerfile(contextPath, dockerfile string) error {
	info, err := os.Stat(contextPath)
	if err != nil {
		return cerr.Wrap(cerr.ExitUsage, err, "build context %q: %v", contextPath, err)
	}
	clean := path.Clean(filepath.ToSlash(dockerfile))
	if clean == "." || path.IsAbs(clean) || clean == ".." || strings.HasPrefix(clean, "../") {
		return cerr.Usage("--dockerfile must name a file inside the build context")
	}
	if info.IsDir() {
		resolvedContext, err := filepath.EvalSymlinks(contextPath)
		if err != nil {
			return cerr.Wrap(cerr.ExitUsage, err, "resolve build context %q: %v", contextPath, err)
		}
		candidate, err := filepath.EvalSymlinks(filepath.Join(resolvedContext, filepath.FromSlash(clean)))
		if err != nil {
			return cerr.Wrap(cerr.ExitUsage, err, "resolve Dockerfile %q: %v", dockerfile, err)
		}
		relative, err := filepath.Rel(resolvedContext, candidate)
		if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return cerr.Usage("--dockerfile must name a file inside the build context")
		}
		file, err := os.Stat(candidate)
		if err != nil {
			return cerr.Wrap(cerr.ExitUsage, err, "stat Dockerfile %q: %v", dockerfile, err)
		}
		if !file.Mode().IsRegular() {
			return cerr.Usage("Dockerfile %q is not a regular file in build context %q", dockerfile, contextPath)
		}
		return nil
	}
	if !info.Mode().IsRegular() || info.Size() > imageContextDirectMaxBytes {
		return nil // The upload preparer reports unsupported entries and size limits.
	}
	file, err := os.Open(contextPath)
	if err != nil {
		return cerr.Wrap(cerr.ExitUsage, err, "open build context %q: %v", contextPath, err)
	}
	defer file.Close()
	archive := tar.NewReader(file)
	for {
		header, err := archive.Next()
		if errors.Is(err, io.EOF) {
			return cerr.Usage("Dockerfile %q is missing from build context %q", dockerfile, contextPath)
		}
		if err != nil {
			return nil // Unknown/compressed archive formats remain server-validated.
		}
		if path.Clean(header.Name) == clean && (header.Typeflag == tar.TypeReg || header.Typeflag == tar.TypeLink || header.Typeflag == tar.TypeSymlink) {
			return nil
		}
	}
}
