// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"net/http"
	"time"
)

type interpreterContextRequest struct {
	ID     string            `json:"id,omitempty"`
	Kernel string            `json:"kernel"`
	Cwd    string            `json:"cwd,omitempty"`
	Env    map[string]string `json:"env,omitempty"`
}

// CreateContext creates independent kernel state. Language is case-sensitive
// and server-defined; unsupported languages return ErrUnsupportedOperation.
// If a named context's response cannot be parsed or bound, the error identifies
// the context that may exist and preserves the failure as its cause.
func (s *Interpreter) CreateContext(
	ctx context.Context, language string, opts *CreateInterpreterContextOptions,
) (*InterpreterContext, error) {
	body := interpreterContextRequest{Kernel: language}
	if opts != nil {
		body.ID, body.Cwd, body.Env = opts.ContextID, opts.Cwd, maps.Clone(opts.Env)
	}
	return s.createContext(ctx, body, 0)
}

func (s *Interpreter) createContext(
	ctx context.Context, body interpreterContextRequest, timeout time.Duration,
) (*InterpreterContext, error) {
	if body.ID != "" {
		if _, err := pathSegment(body.ID); err != nil {
			return nil, err
		}
	}
	var raw JSONValue
	if err := s.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/sessions", Body: body, Result: &raw, Timeout: timeout,
	}); err != nil {
		return nil, err
	}
	id, _, err := parseInterpreterSession(raw)
	var bound *InterpreterContext
	if err == nil {
		bound, err = s.ConnectContext(id, body.Kernel)
	}
	if err != nil {
		if body.ID != "" {
			return nil, wrapError(err, fmt.Sprintf("context %q may have been created", body.ID))
		}
		return nil, err
	}
	return bound, nil
}

// ConnectContext binds an existing context ID and language without a request.
// Empty languages and invalid path IDs return ErrBadRequest.
func (s *Interpreter) ConnectContext(id, language string) (*InterpreterContext, error) {
	if _, err := pathSegment(id); err != nil {
		return nil, err
	}
	if language == "" {
		return nil, badRequest("interpreter context language must be a non-empty string")
	}
	return &InterpreterContext{service: s, id: id, language: language}, nil
}

// ConnectContextJSON reconnects a serialized identity without a request.
// Identities belonging to another sandbox return ErrBadRequest.
func (s *Interpreter) ConnectContextJSON(identity InterpreterContextJSON) (*InterpreterContext, error) {
	if identity.SandboxID != s.sandbox.ID() {
		return nil, badRequest("interpreter context belongs to a different sandbox")
	}
	return s.ConnectContext(identity.ContextID, identity.Language)
}

// ListContexts returns all interpreter contexts in one request. Shell sessions
// are excluded; unknown kernel languages are preserved.
func (s *Interpreter) ListContexts(ctx context.Context) ([]*InterpreterContext, error) {
	var raw JSONValue
	if err := s.sandbox.toolbox().do(ctx, toolboxRequest{Path: "/sessions", Result: &raw}); err != nil {
		return nil, err
	}
	object, err := parseObject(raw, "session list")
	if err != nil {
		return nil, err
	}
	items, err := field[[]json.RawMessage](object, "items", true)
	if err != nil {
		return nil, err
	}
	result := make([]*InterpreterContext, 0, len(items))
	for _, item := range items {
		id, language, parseErr := parseInterpreterSession(item)
		if parseErr != nil {
			return nil, parseErr
		}
		if language == "shell" {
			continue
		}
		bound, bindErr := s.ConnectContext(id, language)
		if bindErr != nil {
			return nil, bindErr
		}
		result = append(result, bound)
	}
	return result, nil
}

// DeleteContext deletes kernel state by ID. ErrSessionNotFound is returned
// unchanged; a failed deletion preserves execution serialization.
func (s *Interpreter) DeleteContext(ctx context.Context, id string) error {
	segment, err := pathSegment(id)
	if err != nil {
		return err
	}
	err = s.sandbox.toolbox().do(ctx, toolboxRequest{Method: http.MethodDelete, Path: "/sessions/" + segment})
	if err == nil || errors.Is(err, ErrSessionNotFound) {
		s.evictQueue(id)
	}
	return err
}

func parseInterpreterSession(raw JSONValue) (string, string, error) {
	object, err := parseObject(raw, "session")
	if err != nil {
		return "", "", err
	}
	id, err := field[string](object, "id", true)
	if err != nil {
		return "", "", err
	}
	if id == "" {
		return "", "", badRequest("response field id is required")
	}
	kernel, err := field[string](object, "kernel", true)
	return id, kernel, err
}
