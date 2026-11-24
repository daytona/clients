// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
)

// ProcessHandle controls one process and is safe for concurrent use. Code handles
// reconnected from an identity support replay, wait and kill, but do not rebuild
// rich interpreter results; use Interpreter.Run for those.
type ProcessHandle struct {
	sandbox *Sandbox
	id      string
	kind    ProcessKind
}

// ProcessHandleJSON is the credential-free, cross-SDK process identity.
type ProcessHandleJSON struct {
	// SandboxID identifies the owning sandbox.
	SandboxID string `json:"sandboxId"`
	// ProcessID identifies the process.
	ProcessID string `json:"processId"`
	// Kind is optional; unknown kinds reconnect without local verb restrictions.
	Kind ProcessKind `json:"kind,omitempty"`
}

// Connect binds a process ID or live alias without a request.
func (s *Processes) Connect(id string) (*ProcessHandle, error) {
	if _, err := pathSegment(id); err != nil {
		return nil, err
	}
	return &ProcessHandle{sandbox: s.sandbox, id: id}, nil
}

// ConnectIdentity reconnects a serialized handle, refusing cross-sandbox identities.
func (s *Processes) ConnectIdentity(identity ProcessHandleJSON) (*ProcessHandle, error) {
	if identity.SandboxID != s.sandbox.ID() {
		return nil, badRequest("process belongs to a different sandbox")
	}
	handle, err := s.Connect(identity.ProcessID)
	if err != nil {
		return nil, err
	}
	if kind := processKind(string(identity.Kind)); kind != ProcessKindUnknown {
		handle.kind = kind
	}
	return handle, nil
}

// ID returns the stable process ID or alias used to connect.
func (h *ProcessHandle) ID() string { return h.id }

// SandboxID returns the owning sandbox ID.
func (h *ProcessHandle) SandboxID() string { return h.sandbox.ID() }

// Kind returns the creation kind, or ProcessKindUnknown for an ID-only connection.
func (h *ProcessHandle) Kind() ProcessKind {
	if h.kind == "" {
		return ProcessKindUnknown
	}
	return h.kind
}

// ToJSON returns an identity that ConnectIdentity accepts on another worker.
func (h *ProcessHandle) ToJSON() ProcessHandleJSON {
	return ProcessHandleJSON{SandboxID: h.sandbox.ID(), ProcessID: h.id, Kind: h.kind}
}

// Get fetches current metadata, including the live cwd of a shell session.
func (h *ProcessHandle) Get(ctx context.Context) (*ProcessInfo, error) {
	segment, err := pathSegment(h.id)
	if err != nil {
		return nil, err
	}
	var raw json.RawMessage
	if err = h.sandbox.toolbox().do(ctx, toolboxRequest{Path: "/processes/" + segment, Result: &raw}); err != nil {
		return nil, err
	}
	return parseProcess(raw)
}

// Delete removes the process and its logs, stopping it if needed. It does not
// hide ErrProcessNotFound or ErrUnsupportedOperation.
func (h *ProcessHandle) Delete(ctx context.Context) error {
	return h.request(ctx, toolboxRequest{Method: http.MethodDelete})
}

// Write sends exact bytes to stdin using base64, returning the accepted byte count.
// The process must have been started with Stdin open. Errors include
// ErrStdinClosed, ErrStdinUnavailable and ErrProcessTerminal.
func (h *ProcessHandle) Write(ctx context.Context, data []byte) (int, error) {
	if err := h.requireStdin(); err != nil {
		return 0, err
	}
	body := struct {
		Data     string `json:"data"`
		Encoding string `json:"encoding"`
	}{base64.StdEncoding.EncodeToString(data), "base64"}
	if err := h.request(ctx, toolboxRequest{Method: http.MethodPost, Path: "/stdin", Body: body}); err != nil {
		return 0, err
	}
	return len(data), nil
}

// CloseStdin sends EOF. It is separate from deleting or disconnecting the process.
// A PTY refuses it: write "\x04" (Ctrl-D) instead, which ends the input in the
// terminal's default (canonical) mode at the start of a line; a program that
// puts the terminal in raw mode receives the byte as data.
func (h *ProcessHandle) CloseStdin(ctx context.Context) error {
	if err := h.requireStdin(); err != nil {
		return err
	}
	body := struct {
		Data string `json:"data"`
		EOF  bool   `json:"eof"`
	}{"", true}
	return h.request(ctx, toolboxRequest{Method: http.MethodPost, Path: "/stdin", Body: body})
}

func (h *ProcessHandle) requireStdin() error {
	switch h.kind {
	case ProcessKindCode, ProcessKindSession:
		return newSDKError(CodeUnsupportedOperation, 400, "stdin is only supported for exec and pty handles")
	case "", ProcessKindUnknown, ProcessKindExec, ProcessKindPTY:
		return nil
	default:
		return nil // A future kind is validated by the server.
	}
}

// Resize changes a PTY's size. Other known kinds return ErrUnsupportedOperation.
func (h *ProcessHandle) Resize(ctx context.Context, size TerminalSize) error {
	if h.kind != "" && h.kind != ProcessKindUnknown && h.kind != ProcessKindPTY {
		return newSDKError(CodeUnsupportedOperation, 400, "resize is only supported for pty handles")
	}
	if err := processTerminal(size); err != nil {
		return err
	}
	return h.request(ctx, toolboxRequest{Method: http.MethodPost, Path: "/resize", Body: size})
}

func (h *ProcessHandle) request(ctx context.Context, request toolboxRequest) error {
	segment, err := pathSegment(h.id)
	if err != nil {
		return err
	}
	spec := request
	spec.Path = "/processes/" + segment + request.Path
	return h.sandbox.toolbox().do(ctx, spec)
}
