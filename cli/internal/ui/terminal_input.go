// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui

import (
	"errors"
	"io"
	"time"
)

var errEscapeTimeout = errors.New("escape sequence timed out")

type promptByte struct {
	value byte
	err   error
}

func (p *terminalPrompt) readByte(timeout time.Duration) (byte, error) {
	if len(p.pending) > 0 {
		b := p.pending[0]
		p.pending = p.pending[1:]
		return b, nil
	}
	if p.reading == nil {
		result := make(chan promptByte, 1)
		p.reading = result
		reader := p.reader
		// Read only on demand, without prefetching input for the next prompt.
		// The buffered result survives an escape timeout. Cleanup cancels the
		// reader and joins this read when cancellation succeeds; unsupported
		// cancellation still cannot block publishing the result after close.
		go func() {
			var b [1]byte
			_, err := io.ReadFull(reader, b[:])
			result <- promptByte{value: b[0], err: err}
		}()
	}
	var deadline <-chan time.Time
	if timeout > 0 {
		timer := time.NewTimer(timeout)
		defer timer.Stop()
		deadline = timer.C
	}
	select {
	case result := <-p.reading:
		p.reading = nil
		return result.value, result.err
	case <-deadline:
		return 0, errEscapeTimeout
	case <-p.ctx.Done():
		return 0, p.ctx.Err()
	}
}
