// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
)

const computerUseA11yMaxDepth = 256

// ComputerUseA11y inspects accessibility trees and controls their nodes.
// Obtain it through Sandbox.ComputerUse().A11y(); methods are concurrency-safe.
type ComputerUseA11y struct{ s *Sandbox }

// Tree retrieves a tree, sending maxDepth=256 by default.
// MaxDepth outside 0–256 or a malformed/deeper response returns ErrBadRequest.
func (a *ComputerUseA11y) Tree(
	ctx context.Context, scope ComputerUseA11yScope, opts *ComputerUseA11yTreeOptions,
) (*ComputerUseA11yTreeResponse, error) {
	maxDepth := computerUseA11yMaxDepth
	if opts != nil && opts.MaxDepth != nil {
		maxDepth = *opts.MaxDepth
	}
	if maxDepth < 0 || maxDepth > computerUseA11yMaxDepth {
		return nil, badRequest("MaxDepth must be an integer between 0 and 256")
	}
	query := url.Values{"scope": {string(scope)}, "maxDepth": {strconv.Itoa(maxDepth)}}
	if opts != nil && opts.PID != nil {
		query.Set("pid", strconv.Itoa(*opts.PID))
	}
	var response JSONValue
	if err := a.s.toolbox().do(ctx, toolboxRequest{
		Path: "/computeruse/a11y/tree", Query: query, Result: &response,
	}); err != nil {
		return nil, err
	}
	wire, err := parseObject(response, "computer-use accessibility tree")
	if err != nil {
		return nil, err
	}
	root, err := field[JSONValue](wire, "root", true)
	if err != nil {
		return nil, err
	}
	truncated, err := field[bool](wire, "truncated", true)
	if err != nil {
		return nil, err
	}
	nodes, err := parseComputerUseA11yNodes([]JSONValue{root}, maxDepth)
	if err != nil {
		return nil, err
	}
	return &ComputerUseA11yTreeResponse{Root: nodes[0], Truncated: truncated}, nil
}

// Find retrieves matching nodes. A malformed response or a match nested more
// than 256 levels below its root returns ErrBadRequest.
func (a *ComputerUseA11y) Find(
	ctx context.Context, scope ComputerUseA11yScope, opts *ComputerUseA11yFindOptions,
) (*ComputerUseA11yFindResponse, error) {
	body := struct {
		Scope ComputerUseA11yScope `json:"scope"`
		ComputerUseA11yFindOptions
		States *[]string `json:"states,omitempty"`
	}{Scope: scope}
	if opts != nil {
		body.ComputerUseA11yFindOptions = *opts
		if opts.States != nil {
			body.States = &opts.States
		}
	}
	var response JSONValue
	if err := a.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/a11y/find", Body: body, Result: &response,
	}); err != nil {
		return nil, err
	}
	wire, err := parseObject(response, "computer-use accessibility find")
	if err != nil {
		return nil, err
	}
	matches, err := field[[]JSONValue](wire, "matches", true)
	if err != nil {
		return nil, err
	}
	truncated, err := field[bool](wire, "truncated", true)
	if err != nil {
		return nil, err
	}
	nodes, err := parseComputerUseA11yNodes(matches, computerUseA11yMaxDepth)
	if err != nil {
		return nil, err
	}
	return &ComputerUseA11yFindResponse{Matches: nodes, Truncated: truncated}, nil
}

// Focus focuses the accessibility node identified by id.
// Sandbox errors are returned unchanged.
func (a *ComputerUseA11y) Focus(ctx context.Context, id string) error {
	body := struct {
		ID string `json:"id"`
	}{ID: id}
	return a.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/a11y/node/focus", Body: body,
	})
}

// Invoke performs a node's default action, or opts.Action when specified.
// Sandbox errors are returned unchanged.
func (a *ComputerUseA11y) Invoke(ctx context.Context, id string, opts *ComputerUseA11yInvokeOptions) error {
	body := struct {
		ID     string  `json:"id"`
		Action *string `json:"action,omitempty"`
	}{ID: id}
	if opts != nil {
		body.Action = opts.Action
	}
	return a.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/a11y/node/invoke", Body: body,
	})
}

// SetValue sets a node's value, including an empty value.
// Sandbox errors are returned unchanged.
func (a *ComputerUseA11y) SetValue(ctx context.Context, id, value string) error {
	body := struct {
		ID    string `json:"id"`
		Value string `json:"value"`
	}{ID: id, Value: value}
	return a.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/a11y/node/value", Body: body,
	})
}
