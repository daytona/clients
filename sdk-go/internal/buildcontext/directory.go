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

// entrySet owns the root used both to collect metadata and to open archive inputs.
type entrySet struct {
	root       *os.Root
	items      []entry
	dockerfile string
}

func directoryEntries(ctx context.Context, directory, dockerfile string) (result *entrySet, err error) {
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	info, err := os.Stat(directory)
	if err != nil {
		return nil, invalid("build context directory cannot be read: "+quote(directory), err)
	}
	resolved, err := filepath.EvalSymlinks(directory)
	if err != nil {
		if denied(err) {
			return nil, invalid("build context directory is not readable: "+quote(directory), err)
		}
		return nil, err
	}
	if err = checkUTF8(resolved); err != nil {
		return nil, err
	}
	root, err := openDirectory(resolved, info)
	if err != nil {
		return nil, invalid("build context directory cannot be read: "+quote(directory), err)
	}
	return walkDirectory(ctx, root, dockerfile)
}

func walkDirectory(ctx context.Context, root *os.Root, dockerfile string) (result *entrySet, err error) {
	defer func() {
		if err != nil {
			err = errors.Join(err, root.Close())
		}
	}()
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	name, err := dockerfileName(root, dockerfile)
	if err != nil {
		return nil, err
	}
	filter, err := directoryFilter(ctx, root, name)
	if err != nil {
		return nil, err
	}
	walk := directoryWalk{ctx: ctx, root: root, filter: filter, omitIgnore: dockerfile == ""}
	if err = walk.visit("", true, nil); err != nil {
		return nil, err
	}
	return &entrySet{root: root, items: walk.entries, dockerfile: name}, nil
}

func dockerfileName(root *os.Root, given string) (string, error) {
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

func directoryFilter(ctx context.Context, root *os.Root, dockerfile string) (*dockerignore.Filter, error) {
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
			filename := filepath.Join(root.Name(), candidate)
			return nil, invalid("build context path is not a regular file: "+quote(filename), err)
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

func readIgnore(ctx context.Context, root *os.Root, candidate string) (patterns []string, err error) {
	info, err := regularFile(root, candidate)
	if err != nil {
		return nil, err
	}
	filename := filepath.Join(root.Name(), candidate)
	// Ignore files may be links, but every component must remain inside the root.
	input, err := openFollowed(root, candidate)
	if err != nil {
		return nil, invalid("build context file is not readable: "+quote(filename), err)
	}
	defer func() { err = errors.Join(err, input.Close()) }()
	opened, err := input.Stat()
	if err != nil {
		return nil, err
	}
	if !opened.Mode().IsRegular() || !os.SameFile(info, opened) {
		return nil, invalid("build context changed: "+quote(filename), nil)
	}
	patterns, err = dockerignore.ReadPatternsFrom(io.LimitReader(&contextReader{ctx: ctx, input: input}, opened.Size()))
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
