// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"time"
	"unicode/utf8"
)

// ForkOptions selects the fork's name, memory copying and observation budget.
type ForkOptions struct {
	// Name is an optional non-blank name; empty uses the server's generated name.
	Name string
	// Memory selects whether to copy memory. Nil uses the platform default:
	// Linux copies memory unless stopped; macOS copies disk only.
	// Explicit true on macOS is refused by the API.
	Memory *bool
	// Wait defaults to 60 seconds. NoWait performs only the POST; WaitForever
	// has no SDK deadline. Positive fractional durations are supported.
	Wait time.Duration
}

// Fork copies this sandbox and returns the fork's last observed handle. A running
// source waits for Started; a paused Linux memory fork waits for Paused; all
// other forks wait for Stopped. Waiting forks refresh the source before POST.
// NoWait skips every GET, including the source refresh. Accepted forks return
// their handle despite SDK wait failures. If the server accepted the request,
// the returned *Sandbox is non-nil even when err is non-nil: delete it or keep it.
// Caller cancellation or deadline expiry remains reachable through errors.Is.
// If the POST itself is interrupted, the outcome is unknown. Fork has no
// idempotency key: inspect existing forks before retrying to avoid duplicates.
// The fork never inherits the source's toolbox override or access token.
func (s *Sandbox) Fork(ctx context.Context, opts *ForkOptions) (*Sandbox, error) {
	if err := s.client.ensureOpen(); err != nil {
		return nil, err
	}
	options, err := prepareForkOptions(opts)
	if err != nil {
		return nil, err
	}
	budget := options.Wait
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	source, err := s.forkSource(ctx, budget)
	if err != nil {
		return nil, err
	}
	body := struct {
		Name   string `json:"name,omitempty"`
		Memory *bool  `json:"memory,omitempty"`
	}{options.Name, clonePointer(options.Memory)}
	var raw JSONValue
	if err = s.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/fork", Body: body, Result: &raw,
	}); err != nil {
		return nil, err
	}
	fork, err := s.parseFork(raw, source)
	if err != nil {
		return nil, err
	}
	if err = acceptedWaitError(ctx, nil); err != nil {
		return fork, err
	}
	if budget == NoWait {
		return fork, nil
	}
	target := SandboxStateStopped
	switch {
	case source.State == SandboxStateStarted:
		target = SandboxStateStarted
	case source.Class == SandboxClassLinux && source.State == SandboxStatePaused &&
		(options.Memory == nil || *options.Memory):
		target = SandboxStatePaused
	}
	return fork.observeFork(ctx, target, budget)
}

func prepareForkOptions(opts *ForkOptions) (ForkOptions, error) {
	options := ForkOptions{}
	if opts != nil {
		options = *opts
	}
	budget, err := lifecycleWaitBudget(options.Wait)
	if err != nil {
		return options, err
	}
	options.Wait = budget
	if options.Name != "" && (createTrim(options.Name) == "" || !utf8.ValidString(options.Name)) {
		return options, badRequest("fork name must be non-blank valid UTF-8 when given")
	}
	return options, nil
}

func (s *Sandbox) forkSource(ctx context.Context, budget time.Duration) (SandboxInfo, error) {
	source := s.Info()
	if budget == NoWait {
		return source, nil
	}
	if source.Class == SandboxClassUnknown && source.RawClass != "" {
		return source, newSDKError(CodeUnsupportedOperation, 400, "unsupported sandbox class "+source.RawClass)
	}
	if err := s.Refresh(ctx); err != nil {
		return source, err
	}
	source = s.Info()
	switch source.Class {
	case SandboxClassLinux, SandboxClassMacOS:
		return source, nil
	case SandboxClassUnknown:
		return source, newSDKError(CodeUnsupportedOperation, 400, "unsupported sandbox class "+source.RawClass)
	default:
		return source, newSDKError(CodeUnsupportedOperation, 400, "unsupported sandbox class "+source.RawClass)
	}
}

func (s *Sandbox) parseFork(raw JSONValue, source SandboxInfo) (*Sandbox, error) {
	object, err := parseObject(raw, "sandbox fork")
	if err != nil {
		return nil, err
	}
	id, err := field[string](object, "id", true)
	if err != nil {
		return nil, err
	}
	if _, pathErr := pathSegment(id); pathErr != nil {
		return nil, badRequest("sandbox fork response is missing the sandbox id")
	}
	status, from := createResponseText(object, "status"), createResponseText(object, "forked_from")
	if from == "" {
		from = s.id
	}
	return s.client.newSandbox(sandboxInit{
		Info: SandboxInfo{
			ID: id, State: sandboxState(status), RawState: status,
			Class: source.Class, RawClass: source.RawClass, ForkedFrom: from, Region: regionOf(id),
		},
		ToolboxURL: createResponseText(object, "toolbox_proxy_url"),
	})
}

func (s *Sandbox) observeFork(ctx context.Context, target SandboxState, budget time.Duration) (*Sandbox, error) {
	started := time.Now()
	from := s.Info().ForkedFrom
	// Refreshes need not repeat forked_from; keep the already known provenance.
	defer func() {
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.info.ForkedFrom == "" {
			s.info.ForkedFrom = from
		}
	}()
	readCtx := ctx
	if budget != WaitForever {
		var cancel context.CancelFunc
		readCtx, cancel = context.WithTimeout(ctx, budget)
		defer cancel()
	}
	s.mu.RLock()
	toolbox := s.toolboxURL
	s.mu.RUnlock()
	if err := s.Refresh(readCtx); err != nil {
		return s, acceptedWaitError(ctx, err)
	}
	if toolbox != "" {
		s.mu.Lock()
		s.toolboxURL = toolbox
		s.mu.Unlock()
	}
	remaining := budget
	if budget != WaitForever {
		remaining -= time.Since(started)
	}
	if s.State() != target && (remaining > 0 || budget == WaitForever) {
		err := s.Wait(ctx, &WaitOptions{Until: []SandboxState{target}, Timeout: remaining})
		return s, acceptedWaitError(ctx, err)
	}
	return s, acceptedWaitError(ctx, nil)
}
