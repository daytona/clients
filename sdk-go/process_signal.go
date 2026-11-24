// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
)

// Kill sends a signal (TERM by default), with optional escalation. INT interrupts
// code cells and session commands; a terminal process returns ErrProcessTerminal.
func (h *ProcessHandle) Kill(ctx context.Context, opts *KillProcessOptions) error {
	options := KillProcessOptions{}
	if opts != nil {
		options = *opts
	}
	signal := options.Signal
	if signal == "" {
		signal = ProcessSignalTerminate
	}
	wire, err := processSignalWire(signal)
	if err != nil {
		return err
	}
	body := struct {
		Signal          string `json:"signal"`
		EscalateAfterMs *int64 `json:"escalateAfterMs,omitempty"`
		EscalateTo      string `json:"escalateTo,omitempty"`
	}{Signal: wire}
	if options.EscalateAfter == nil && options.EscalateTo != "" {
		// The target would never be sent without a delay.
		return badRequest("EscalateTo needs EscalateAfter")
	}
	if options.EscalateAfter != nil {
		if *options.EscalateAfter < 0 {
			return badRequest("EscalateAfter must be non-negative")
		}
		delay := wireMilliseconds(*options.EscalateAfter)
		if delay > 600000 {
			return badRequest("EscalateAfter must round to at most 600000 milliseconds")
		}
		body.EscalateAfterMs = &delay
		target := options.EscalateTo
		if target == "" {
			target = ProcessSignalKill
		}
		body.EscalateTo, err = processSignalWire(target)
		if err != nil {
			return err
		}
	}
	return h.request(ctx, toolboxRequest{Method: http.MethodPost, Path: "/signals", Body: body})
}

// processSignalWire spells a bare signal name (matched case-insensitively) as
// the wire does, with the SIG prefix.
func processSignalWire(signal ProcessSignal) (string, error) {
	name := strings.ToUpper(string(signal))
	if processSignal(name) == ProcessSignalUnknown || name == "" {
		return "", badRequest("Signal must be TERM, KILL, INT, HUP, QUIT, USR1 or USR2 without a SIG prefix")
	}
	return "SIG" + name, nil
}
