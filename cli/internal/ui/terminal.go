// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"time"
	"unicode/utf8"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/muesli/cancelreader"
	"golang.org/x/term"
)

const (
	keyAbort     = "abort"
	keyEnter     = "enter"
	keyEscape    = "escape"
	keyBackspace = "backspace"
	keyUp        = "up"
	keyDown      = "down"
	keyLeft      = "left"
	keyRight     = "right"
	keyHome      = "home"
	keyEnd       = "end"
	keyPaste     = "paste"
)

type terminalPrompt struct {
	ctx        context.Context
	in         *os.File
	reader     cancelreader.CancelReader
	stopCancel func()
	out        io.Writer
	state      *term.State
	pending    []byte
}

// Keep terminal probing out of the prompt dependency graph. In particular,
// Bubble Tea v1 queries the background in init(), even when no prompt is used.
// Prompts use terminal-default colors and never detect the background.
func newTerminalPrompt(ctx context.Context) (*terminalPrompt, error) {
	if err := cerr.FromContext(ctx); err != nil {
		return nil, err
	}
	state, err := term.MakeRaw(int(os.Stdin.Fd()))
	if err != nil {
		return nil, fmt.Errorf("open terminal prompt: %w", err)
	}
	reader, err := cancelreader.NewReader(os.Stdin)
	if err != nil {
		return nil, errors.Join(err, term.Restore(int(os.Stdin.Fd()), state))
	}
	// Preserve the prompt's bracketed-paste protocol without querying any
	// terminal properties. Input is already raw when the UI-ready signal goes out.
	if _, err := fmt.Fprint(os.Stdout, "\x1b[?2004h"); err != nil {
		return nil, errors.Join(err, reader.Close(), term.Restore(int(os.Stdin.Fd()), state))
	}
	return &terminalPrompt{
		ctx: ctx, in: os.Stdin, reader: reader, out: os.Stdout, state: state,
		stopCancel: cancelReadOnContext(ctx, reader),
	}, nil
}

func (p *terminalPrompt) close() error {
	p.stopCancel()
	readErr := p.reader.Close()
	err := term.Restore(int(p.in.Fd()), p.state)
	_, writeErr := fmt.Fprint(p.out, "\x1b[?2004l\r\n")
	return errors.Join(readErr, err, writeErr)
}

func (p *terminalPrompt) render(line string) error {
	_, err := fmt.Fprint(p.out, "\r\x1b[2K", line)
	return err
}

func (p *terminalPrompt) readKey() (key string, err error) {
	defer func() {
		if contextErr := cerr.FromContext(p.ctx); contextErr != nil {
			key, err = "", contextErr
		}
	}()
	var first [1]byte
	if len(p.pending) > 0 {
		first[0], p.pending = p.pending[0], p.pending[1:]
	} else {
		if _, err := io.ReadFull(p.reader, first[:]); err != nil {
			return "", fmt.Errorf("read terminal prompt: %w", err)
		}
	}
	switch first[0] {
	case 3:
		return keyAbort, nil
	case '\r', '\n', '\t':
		return keyEnter, nil
	case 8, 127:
		return keyBackspace, nil
	case 27:
		key, err := p.readEscape()
		if err != nil || key != keyPaste {
			return key, err
		}
		// The escape-sequence deadline ends before reading the payload: a
		// slow or large paste must never become individual confirmation keys.
		return keyPaste, p.discardPaste()
	}
	data := []byte{first[0]}
	for !utf8.FullRune(data) {
		if _, err := io.ReadFull(p.reader, first[:]); err != nil {
			return "", fmt.Errorf("read prompt character: %w", err)
		}
		data = append(data, first[0])
	}
	return string(data), nil
}

func (p *terminalPrompt) readEscape() (key string, err error) {
	reader, err := cancelreader.NewReader(p.in)
	if err != nil {
		return "", fmt.Errorf("read prompt escape: %w", err)
	}
	defer func() { err = errors.Join(err, reader.Close()) }()
	stopCancel := cancelReadOnContext(p.ctx, reader)
	defer stopCancel()
	// A bare Escape must not block waiting for an arrow-key suffix. Cancel
	// the read without consuming input; join the timer before closing reader.
	done := make(chan struct{})
	timer := time.AfterFunc(50*time.Millisecond, func() {
		reader.Cancel()
		close(done)
	})
	defer func() {
		if !timer.Stop() {
			<-done
		}
	}()
	key, err = p.readEscapeSequence(reader)
	if errors.Is(err, cancelreader.ErrCanceled) {
		return keyEscape, nil
	}
	return key, err
}

func (p *terminalPrompt) readEscapeSequence(reader io.Reader) (string, error) {
	var b [1]byte
	if _, err := io.ReadFull(reader, b[:]); err != nil {
		return "", err
	}
	if b[0] != '[' && b[0] != 'O' {
		p.pending = append(p.pending, b[0])
		return keyEscape, nil
	}
	sequence := ""
	for range 16 {
		if _, err := io.ReadFull(reader, b[:]); err != nil {
			return "", err
		}
		sequence += string(b[0])
		switch b[0] {
		case 'A':
			return keyUp, nil
		case 'B':
			return keyDown, nil
		case 'C':
			return keyRight, nil
		case 'D':
			return keyLeft, nil
		case 'H':
			return keyHome, nil
		case 'F':
			return keyEnd, nil
		case '~':
			switch sequence {
			case "1~", "7~":
				return keyHome, nil
			case "4~", "8~":
				return keyEnd, nil
			case "200~":
				return keyPaste, nil
			}
		}
		if b[0] >= 0x40 && b[0] <= 0x7e {
			return keyEscape, nil
		}
	}
	return keyEscape, nil
}

func (p *terminalPrompt) discardPaste() error {
	const end = "\x1b[201~"
	matched := 0
	var b [1]byte
	for matched < len(end) {
		if _, err := io.ReadFull(p.reader, b[:]); err != nil {
			return fmt.Errorf("read terminal paste: %w", err)
		}
		if b[0] == end[matched] {
			matched++
		} else if b[0] == end[0] {
			matched = 1
		} else {
			matched = 0
		}
	}
	return nil
}

func cancelReadOnContext(ctx context.Context, reader cancelreader.CancelReader) func() {
	done := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		reader.Cancel()
		close(done)
	})
	return func() {
		// AfterFunc's stop does not wait for a running callback. Join it so
		// it cannot write its cancellation signal after reader.Close().
		if !stop() {
			<-done
		}
	}
}
