// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui

import (
	"github.com/charmbracelet/huh"
)

// Confirm asks a y/N question on the TTY. The safe answer (No) is focused
// by default, so Enter alone never confirms a destructive verb; `y`/`Y`
// or moving to Yes and pressing Enter does. Runs inside a Form so the
// field's key map (←/→ toggle, y/n) is fully wired.
func Confirm(title string) (bool, error) {
	ok := false
	field := huh.NewConfirm().Title(title).Affirmative("Yes").Negative("No").Value(&ok)
	err := huh.NewForm(huh.NewGroup(field)).WithShowHelp(true).Run()
	if err != nil {
		if err == huh.ErrUserAborted {
			return false, nil
		}
		return false, err
	}
	return ok, nil
}

// Option is one picker entry.
type Option struct {
	Label string
	Value string
}

// Select shows a single-choice picker on the TTY.
func Select(title string, opts []Option) (string, error) {
	hopts := make([]huh.Option[string], 0, len(opts))
	for _, o := range opts {
		hopts = append(hopts, huh.NewOption(o.Label, o.Value))
	}
	var v string
	err := huh.NewSelect[string]().Title(title).Options(hopts...).Value(&v).Run()
	return v, err
}
