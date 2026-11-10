// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/daytona/clients/cli/internal/output"
	"golang.org/x/term"
)

// Confirm asks a y/N question on the TTY. The safe answer (No) is focused
// by default, so Enter alone never confirms a destructive verb; `y`/`Y`
// or moving to Yes and pressing Enter does.
func Confirm(title string) (bool, error) {
	// Compatibility boundary: command callers should pass their invocation
	// context to ConfirmContext instead.
	return ConfirmContext(context.Background(), title)
}

// ConfirmContext is Confirm with cancellation of the terminal input read.
func ConfirmContext(ctx context.Context, title string) (ok bool, err error) {
	p, err := newTerminalPrompt(ctx)
	if err != nil {
		return false, err
	}
	defer func() { err = errors.Join(err, p.close()) }()
	for {
		choices := "Yes / [No]"
		if ok {
			choices = "[Yes] / No"
		}
		if err := p.render(output.SanitizeLine(title) + " " + choices + " (y/n, ←/→, enter)"); err != nil {
			return false, err
		}
		key, err := p.readKey()
		if err != nil {
			return false, err
		}
		switch key {
		case "y", "Y":
			return true, nil
		case "n", "N", keyAbort, "\x04":
			return false, nil
		case keyEnter:
			return ok, nil
		case keyLeft, keyRight, "h", "l":
			ok = !ok
		}
	}
}

// Option is one picker entry.
type Option struct {
	Label string
	Value string
}

// Select shows a single-choice picker on the TTY.
func Select(title string, opts []Option) (string, error) {
	// Compatibility boundary: command callers should pass their invocation
	// context to SelectContext instead.
	return SelectContext(context.Background(), title, opts)
}

// SelectContext is Select with cancellation of the terminal input read.
func SelectContext(ctx context.Context, title string, opts []Option) (value string, err error) {
	if len(opts) == 0 {
		return "", errPromptOptions
	}
	p, err := newTerminalPrompt(ctx)
	if err != nil {
		return "", err
	}
	defer func() { err = errors.Join(err, p.close()) }()
	_, height, sizeErr := term.GetSize(int(p.in.Fd()))
	if sizeErr != nil || height < 1 {
		height = 24
	}
	selected := 0
	filter := ""
	filtering := false
	for {
		visible := make([]Option, 0, len(opts))
		for _, option := range opts {
			if strings.Contains(strings.ToLower(option.Label), strings.ToLower(filter)) {
				visible = append(visible, option)
			}
		}
		selected = min(selected, max(0, len(visible)-1))
		label := "(no matches)"
		if len(visible) > 0 {
			label = visible[selected].Label
		}
		line := fmt.Sprintf("%s: %s [%d/%d] (↑/↓, enter, / filter)", title, label, min(selected+1, len(visible)), len(visible))
		if filtering || filter != "" {
			line += " /" + filter
		}
		if err := p.render(output.SanitizeLine(line)); err != nil {
			return "", err
		}
		key, err := p.readKey()
		if err != nil {
			return "", err
		}
		if key == keyAbort {
			return "", ErrPromptAborted
		}
		if filtering {
			switch key {
			case keyEnter:
				if len(visible) > 0 {
					return visible[selected].Value, nil
				}
			case keyEscape:
				filtering = false
				continue
			case keyBackspace:
				runes := []rune(filter)
				if len(runes) > 0 {
					filter = string(runes[:len(runes)-1])
				}
				continue
			default:
				if len([]rune(key)) == 1 && key >= " " {
					filter += key
					selected = 0
					continue
				}
			}
		}
		switch key {
		case keyEnter:
			if len(visible) > 0 {
				return visible[selected].Value, nil
			}
		case keyUp, "k", "\x0b", "\x10":
			selected--
			if selected < 0 {
				selected = max(0, len(visible)-1)
			}
		case keyDown, "j", "\x0e":
			selected++
			if selected >= len(visible) {
				selected = 0
			}
		case keyHome, "g":
			selected = 0
		case keyEnd, "G":
			selected = max(0, len(visible)-1)
		case "\x15":
			selected = max(0, selected-max(1, min(len(visible), height-4)/2))
		case "\x04":
			selected = min(max(0, len(visible)-1), selected+max(1, min(len(visible), height-4)/2))
		case "/":
			filtering = true
		case keyEscape:
			filter = ""
		}
	}
}

// ErrPromptAborted reports that the user cancelled a selection.
var ErrPromptAborted = errors.New("user aborted")

var errPromptOptions = errors.New("selection requires at least one option")
