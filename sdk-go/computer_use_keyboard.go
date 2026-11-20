// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"time"
)

// ComputerUseKeyboard sends desktop keyboard input.
type ComputerUseKeyboard struct{ s *Sandbox }

// ComputerUseKeyboardModifier is a held modifier key.
type ComputerUseKeyboardModifier string

const (
	// ComputerUseModifierUnknown denotes an unrecognized modifier.
	ComputerUseModifierUnknown ComputerUseKeyboardModifier = "unknown"
	// ComputerUseModifierCtrl is Control.
	ComputerUseModifierCtrl ComputerUseKeyboardModifier = "ctrl"
	// ComputerUseModifierAlt is Alt.
	ComputerUseModifierAlt ComputerUseKeyboardModifier = "alt"
	// ComputerUseModifierShift is Shift.
	ComputerUseModifierShift ComputerUseKeyboardModifier = "shift"
	// ComputerUseModifierCmd is Command.
	ComputerUseModifierCmd ComputerUseKeyboardModifier = "cmd"
)

// ComputerUseTypeOptions controls typing cadence.
type ComputerUseTypeOptions struct {
	// Delay is the pause between key presses, rounded up to milliseconds.
	// Zero uses the server default; negative durations return ErrBadRequest.
	Delay time.Duration
}

// ComputerUseKeyOptions selects held modifier keys.
type ComputerUseKeyOptions struct {
	// Modifiers is omitted when nil; an empty non-nil slice sends an empty list.
	Modifiers []ComputerUseKeyboardModifier
}

// Type types text. Nil options use the server's default inter-key delay.
func (k *ComputerUseKeyboard) Type(ctx context.Context, text string, opts *ComputerUseTypeOptions) error {
	body := struct {
		Text  string `json:"text"`
		Delay *int64 `json:"delay,omitempty"`
	}{Text: text}
	if opts != nil && opts.Delay != 0 {
		if opts.Delay < 0 {
			return badRequest("delay must be non-negative")
		}
		delay := wireMilliseconds(opts.Delay)
		body.Delay = &delay
	}
	return k.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/keyboard/type", Body: body,
	})
}

// Key presses key with optional modifiers.
func (k *ComputerUseKeyboard) Key(ctx context.Context, key string, opts *ComputerUseKeyOptions) error {
	body := struct {
		Key       string                         `json:"key"`
		Modifiers *[]ComputerUseKeyboardModifier `json:"modifiers,omitempty"`
	}{Key: key}
	if opts != nil && opts.Modifiers != nil {
		body.Modifiers = &opts.Modifiers
	}
	return k.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/keyboard/key", Body: body,
	})
}

// Hotkey sends a platform hotkey expression, for example "ctrl+c".
func (k *ComputerUseKeyboard) Hotkey(ctx context.Context, keys string) error {
	body := struct {
		Keys string `json:"keys"`
	}{Keys: keys}
	return k.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/keyboard/hotkey", Body: body,
	})
}
