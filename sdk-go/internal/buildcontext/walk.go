// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"os"
	"path"
	"path/filepath"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

type directoryWalk struct {
	ctx        context.Context
	root       string
	filter     *dockerignore.Filter
	omitIgnore bool
	entries    []entry
}

func (w *directoryWalk) visit(relative string, kept bool, parent dockerignore.Results) error {
	if err := w.ctx.Err(); err != nil {
		return err
	}
	directory := filepath.Join(w.root, filepath.FromSlash(relative))
	names, err := os.ReadDir(directory)
	if err != nil {
		if denied(err) {
			if !kept {
				return nil
			}
			return invalid("build context directory is not readable: "+quote(directory), err)
		}
		return err
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
	current := filepath.Join(w.root, filepath.FromSlash(rel))
	if err := checkUTF8(current); err != nil {
		return err
	}
	decision, err := w.filter.Keep(rel, parent)
	if err != nil {
		return err
	}
	if decision.Keep && (!w.omitIgnore || rel != ".dockerignore") {
		item, collectErr := collectEntry(current, rel)
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
			item, collectErr := collectEntry(current, rel)
			if collectErr != nil {
				return collectErr
			}
			w.entries = append(w.entries, item)
		}
	}
	return nil
}
