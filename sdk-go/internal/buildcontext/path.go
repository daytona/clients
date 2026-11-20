// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"unicode"
	"unicode/utf8"
)

func quote(value string) string {
	data, err := json.Marshal(strings.ToValidUTF8(value, "\ufffd"))
	if err != nil {
		return `""`
	}
	// JSON's optional HTML escaping is unnecessary in errors.
	return strings.NewReplacer(`\u003c`, "<", `\u003e`, ">", `\u0026`, "&").Replace(string(data))
}

func checkUTF8(value string) error {
	if !utf8.ValidString(value) {
		return invalid("build context path is not valid UTF-8: "+quote(value), nil)
	}
	return nil
}

func blank(value string) bool {
	return strings.TrimFunc(value, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }) == ""
}

func absolutePath(value string) (string, error) {
	if err := checkUTF8(value); err != nil {
		return "", err
	}
	if blank(value) {
		return "", invalid("build context path must be a non-empty string", nil)
	}
	if strings.ContainsRune(value, 0) {
		return "", invalid("build context path must not contain a NUL character", nil)
	}
	expanded := value
	if value == "~" || strings.HasPrefix(value, "~/") ||
		filepath.Separator == '\\' && strings.HasPrefix(value, `~\`) {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		expanded = home + value[1:]
	}
	return filepath.Abs(expanded)
}

func missing(err error) bool {
	return errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ENOTDIR) || errors.Is(err, syscall.ELOOP)
}

func denied(err error) bool {
	return errors.Is(err, os.ErrPermission)
}

func fileError(filename string, err error) error {
	if denied(err) {
		return invalid("build context file is not readable: "+quote(filename), err)
	}
	return err
}

func regularFile(root, relative string) (string, error) {
	filename := filepath.Join(root, relative)
	// Stat reports a typed ELOOP; filepath.EvalSymlinks uses an untyped error
	// when its own traversal limit is reached.
	info, err := os.Stat(filename)
	if err != nil {
		return "", fileError(filename, err)
	}
	resolved, err := filepath.EvalSymlinks(filename)
	if err != nil {
		return "", fileError(filename, err)
	}
	if err = checkUTF8(resolved); err != nil {
		return "", err
	}
	inside, err := filepath.Rel(root, resolved)
	if err != nil {
		return "", err
	}
	if inside == ".." || strings.HasPrefix(inside, ".."+string(filepath.Separator)) {
		return "", invalid("build context file leaves its local source: "+quote(filename), nil)
	}
	if !info.Mode().IsRegular() {
		return "", invalid("build context path is not a regular file: "+quote(filename), nil)
	}
	return filename, nil
}

func permissionBits(mode os.FileMode) int64 {
	bits := int64(mode.Perm())
	if mode&os.ModeSetuid != 0 {
		bits |= 0o4000
	}
	if mode&os.ModeSetgid != 0 {
		bits |= 0o2000
	}
	if mode&os.ModeSticky != 0 {
		bits |= 0o1000
	}
	return bits
}
