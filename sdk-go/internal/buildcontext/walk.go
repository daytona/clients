// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"io/fs"
	"os"
	"path"
	"path/filepath"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

type directoryWalk struct {
	ctx        context.Context
	root       *os.Root
	filter     *dockerignore.Filter
	omitIgnore bool
	entries    []entry
}

func (w *directoryWalk) visit(relative string, kept bool, parent dockerignore.Results) error {
	if err := w.ctx.Err(); err != nil {
		return err
	}
	if relative != "" {
		if _, err := collectDirectory(w.root, relative); err != nil {
			return err
		}
	}
	directory := filepath.Join(w.root.Name(), filepath.FromSlash(relative))
	names, err := fs.ReadDir(w.root.FS(), path.Join(".", relative))
	if err != nil {
		if denied(err) {
			if !kept {
				return nil
			}
			return invalid("build context directory is not readable: "+quote(directory), err)
		}
		return invalid("build context directory changed: "+quote(directory), err)
	}
	for _, name := range names {
		if err = w.ctx.Err(); err != nil {
			return err
		}
		rel := path.Join(relative, name.Name())
		if err = w.visitChild(rel, name, parent); err != nil {
			return err
		}
	}
	return nil
}

func (w *directoryWalk) visitChild(rel string, name os.DirEntry, parent dockerignore.Results) error {
	current := filepath.Join(w.root.Name(), filepath.FromSlash(rel))
	if err := checkUTF8(current); err != nil {
		return err
	}
	decision, err := w.filter.Keep(rel, parent)
	if err != nil {
		return err
	}
	if decision.Keep && (!w.omitIgnore || rel != ".dockerignore") {
		item, collectErr := collectEntry(w.root, rel)
		if collectErr != nil {
			return collectErr
		}
		w.entries = append(w.entries, item)
	}
	if name.IsDir() && decision.Descend {
		before := len(w.entries)
		if err = w.visit(rel, decision.Keep, decision.State); err != nil {
			return err
		}
		if !decision.Keep && len(w.entries) > before {
			item, collectErr := collectDirectory(w.root, rel)
			if collectErr != nil {
				return collectErr
			}
			w.entries = append(w.entries, item)
		}
	}
	return nil
}

func collectDirectory(root *os.Root, relative string) (entry, error) {
	item, err := collectEntry(root, relative)
	if err != nil {
		return entry{}, err
	}
	if item.kind != dirEntry {
		return entry{}, invalid("build context directory changed: "+quote(item.local), nil)
	}
	return item, nil
}
