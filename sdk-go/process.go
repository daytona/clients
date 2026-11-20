// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"iter"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// Processes manages commands and persistent shells in one sandbox.
type Processes struct{ sandbox *Sandbox }

// Process returns the sandbox's process namespace.
func (s *Sandbox) Process() *Processes { return &Processes{sandbox: s} }

// Start launches a background shell command and returns immediately. Use
// StartArgs for untrusted arguments; use CreateSession to retain shell state.
// ExecutionTimeout stops the process tree; context cancellation only stops the request.
func (s *Processes) Start(ctx context.Context, command string, opts *ProcessStartOptions) (*ProcessHandle, error) {
	body, err := processStartBody(&command, nil, opts)
	if err != nil {
		return nil, err
	}
	body.Kind = ""
	return s.start(ctx, body, 0)
}

// StartArgs launches argv without a shell. argv must contain a non-empty executable.
func (s *Processes) StartArgs(
	ctx context.Context, argv []string, opts *ProcessStartOptions,
) (*ProcessHandle, error) {
	if len(argv) == 0 {
		return nil, badRequest("argv must not be empty")
	}
	body, err := processStartBody(nil, argv, opts)
	if err != nil {
		return nil, err
	}
	body.Kind = ""
	return s.start(ctx, body, 0)
}

func (s *Processes) start(
	ctx context.Context, body processCreateRequest, timeout time.Duration,
) (*ProcessHandle, error) {
	var raw json.RawMessage
	if err := s.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/processes", Body: body, Result: &raw, Timeout: timeout,
	}); err != nil {
		return nil, err
	}
	info, err := parseProcess(raw)
	if err != nil {
		return nil, err
	}
	return s.startedHandle(info, body), nil
}

func (s *Processes) startedHandle(info *ProcessInfo, body processCreateRequest) *ProcessHandle {
	kind := info.Kind
	if kind == ProcessKindUnknown {
		kind = body.Kind
		if kind == "" {
			kind = ProcessKindExec
		}
	}
	return &ProcessHandle{sandbox: s.sandbox, id: info.ID, kind: kind}
}

// Get fetches metadata by process ID or live alias. Missing processes return ErrProcessNotFound.
func (s *Processes) Get(ctx context.Context, id string) (*ProcessInfo, error) {
	handle, err := s.Connect(id)
	if err != nil {
		return nil, err
	}
	return handle.Get(ctx)
}

// Delete stops a process and removes its record and logs. A running code cell
// may return ErrUnsupportedOperation; interrupt it first.
func (s *Processes) Delete(ctx context.Context, id string) error {
	handle, err := s.Connect(id)
	if err != nil {
		return err
	}
	return handle.Delete(ctx)
}

// List returns one envelope of processes. The server does not paginate this list.
func (s *Processes) List(ctx context.Context, opts *ListProcessesOptions) (*Page[ProcessInfo], error) {
	query, err := processListQuery(opts)
	if err != nil {
		return nil, err
	}
	var raw json.RawMessage
	if err = s.sandbox.toolbox().do(ctx, toolboxRequest{Path: "/processes", Query: query, Result: &raw}); err != nil {
		return nil, err
	}
	object, err := parseObject(raw, "process list")
	if err != nil {
		return nil, err
	}
	items, err := field[[]json.RawMessage](object, "items", true)
	if err != nil {
		return nil, err
	}
	page := &Page[ProcessInfo]{Items: make([]ProcessInfo, 0, len(items))}
	for _, item := range items {
		info, parseErr := parseProcess(item)
		if parseErr != nil {
			return nil, parseErr
		}
		if opts == nil || opts.Tag == "" || info.Tag == opts.Tag {
			page.Items = append(page.Items, *info)
		}
	}
	return page, nil
}

// ListAll iterates List lazily. Breaking iteration performs no additional requests.
func (s *Processes) ListAll(ctx context.Context, opts *ListProcessesOptions) iter.Seq2[ProcessInfo, error] {
	var copied *ListProcessesOptions
	if opts != nil {
		value := *opts
		value.PID = clonePointer(opts.PID)
		copied = &value
	}
	return paginate(ctx, "", func(ctx context.Context, _ string) (*Page[ProcessInfo], error) {
		return s.List(ctx, copied)
	})
}

func processListQuery(opts *ListProcessesOptions) (url.Values, error) {
	query := make(url.Values)
	if opts == nil {
		return query, nil
	}
	if opts.Tag != "" {
		if err := processTag(opts.Tag); err != nil {
			return nil, err
		}
	}
	if opts.State != "" && opts.State != "all" && opts.State != "running" && opts.State != "terminal" {
		return nil, badRequest("state must be running, terminal or all")
	}
	if opts.Kind != "" && processKind(string(opts.Kind)) == ProcessKindUnknown {
		return nil, badRequest("kind must be exec, pty, code or session")
	}
	for key, value := range map[string]string{
		"tag": opts.Tag, "state": opts.State, "kind": string(opts.Kind), "sessionId": opts.SessionID, "name": opts.Name,
	} {
		if value != "" {
			query.Set(key, value)
		}
	}
	if opts.PID != nil {
		query.Set("pid", strconv.Itoa(*opts.PID))
	}
	return query, nil
}

// KillAll stops tagged processes and their descendants, retaining logs and records.
// It returns stopped IDs. ErrProcessKillIncomplete is safe to retry;
// ErrUnsupportedOperation indicates unavailable process containment (including macOS).
func (s *Processes) KillAll(ctx context.Context, tag string, opts *KillAllProcessesOptions) ([]string, error) {
	if err := processTag(tag); err != nil {
		return nil, err
	}
	var timeout time.Duration
	if opts != nil {
		timeout = opts.Timeout
	}
	if timeout < 0 || timeout > 60*time.Second {
		return nil, badRequest("kill timeout must be positive and at most 60 seconds; zero selects the default")
	}
	body := struct {
		Tag       string `json:"tag"`
		TimeoutMs int64  `json:"timeoutMs,omitempty"`
	}{tag, wireMilliseconds(timeout)}
	budget := time.Duration(body.TimeoutMs) * time.Millisecond
	if budget == 0 {
		budget = 10 * time.Second
	}
	var raw json.RawMessage
	if err := s.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/processes:kill", Body: body, Result: &raw, Timeout: budget + 5*time.Second,
	}); err != nil {
		return nil, err
	}
	object, err := parseObject(raw, "kill processes")
	if err != nil {
		return nil, err
	}
	return processStringList(object, "processIds")
}
