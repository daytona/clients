// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"errors"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
	"syscall"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

func directoryEntries(ctx context.Context, root, dockerfile string) ([]entry, string, error) {
	if err := ctx.Err(); err != nil {
		return nil, "", err
	}
	if _, err := os.Stat(root); err != nil {
		return nil, "", invalid("build context directory cannot be read: "+quote(root), err)
	}
	resolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		if denied(err) {
			return nil, "", invalid("build context directory is not readable: "+quote(root), err)
		}
		return nil, "", err
	}
	if err = checkUTF8(resolved); err != nil {
		return nil, "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return nil, "", err
	}
	if !info.IsDir() {
		return nil, "", invalid(quote(root)+" is not a directory", nil)
	}
	name, err := dockerfileName(resolved, dockerfile)
	if err != nil {
		return nil, "", err
	}
	filter, err := directoryFilter(ctx, resolved, name)
	if err != nil {
		return nil, "", err
	}
	walk := directoryWalk{ctx: ctx, root: resolved, filter: filter, omitIgnore: dockerfile == ""}
	if err = walk.visit("", true, nil); err != nil {
		return nil, "", err
	}
	return walk.entries, name, nil
}

func dockerfileName(root, given string) (string, error) {
	if given == "" { // A builder directory has no selected Dockerfile.
		return "", nil
	}
	clean, err := relativeDockerfilePath(given)
	if err != nil {
		return "", err
	}
	if _, err = regularFile(root, filepath.FromSlash(clean)); err != nil {
		if missing(err) {
			return "", invalid("dockerfile "+quote(given)+" does not exist in the build context", err)
		}
		return "", err
	}
	return clean, nil
}

func relativeDockerfilePath(given string) (string, error) {
	if blank(given) {
		return "", invalid("dockerfile must be a non-empty path", nil)
	}
	if err := checkUTF8(given); err != nil {
		return "", err
	}
	clean := path.Clean(filepath.ToSlash(given))
	windowsAbsolute := strings.HasPrefix(given, `\`) ||
		len(given) >= 3 && given[1] == ':' && (given[2] == '/' || given[2] == '\\')
	if strings.ContainsRune(given, 0) || filepath.IsAbs(given) || windowsAbsolute ||
		clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		return "", invalid("dockerfile must be a relative path inside the build context", nil)
	}
	return clean, nil
}

func directoryFilter(ctx context.Context, root, dockerfile string) (*dockerignore.Filter, error) {
	candidates := []string{".dockerignore"}
	var always []string
	if dockerfile != "" {
		candidates = append([]string{dockerfile + ".dockerignore"}, candidates...)
		always = []string{dockerfile, ".dockerignore"}
	}
	var patterns []string
	for _, candidate := range candidates {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		read, err := readIgnore(ctx, root, candidate)
		if errors.Is(err, syscall.ELOOP) {
			return nil, invalid("build context path is not a regular file: "+quote(filepath.Join(root, candidate)), err)
		}
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ENOTDIR) {
			continue
		}
		if err != nil {
			return nil, err
		}
		patterns = read
		if dockerfile != "" {
			always = append(always, candidate)
		}
		break
	}
	matcher, err := dockerignore.New(patterns)
	if err != nil {
		return nil, err
	}
	return dockerignore.NewFilter(matcher, always), nil
}

func readIgnore(ctx context.Context, root, candidate string) (patterns []string, err error) {
	filename, err := regularFile(root, candidate)
	if err != nil {
		return nil, err
	}
	resolved, err := filepath.EvalSymlinks(filename)
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return nil, fileError(filename, err)
	}
	item := entry{local: resolved, info: info}
	input, size, err := item.open()
	if err != nil {
		return nil, err
	}
	defer func() { err = errors.Join(err, input.Close()) }()
	patterns, err = dockerignore.ReadPatternsFrom(io.LimitReader(&contextReader{ctx: ctx, input: input}, size))
	return patterns, fileError(filename, err)
}

type contextReader struct {
	ctx   context.Context
	input io.Reader
}

func (r *contextReader) Read(buffer []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.input.Read(buffer[:min(len(buffer), 64*1024)])
}
