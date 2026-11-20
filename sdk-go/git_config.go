// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"

	wire "go.daytona.com/sdk/v2/internal/gen/toolbox"
)

// GitConfigScope selects a global or repository-local Git configuration.
type GitConfigScope string

const (
	// GitConfigScopeGlobal selects the user's global configuration (the default).
	GitConfigScopeGlobal GitConfigScope = "global"
	// GitConfigScopeLocal selects the repository configuration.
	GitConfigScopeLocal GitConfigScope = "local"
)

// GitConfigOptions selects the Git configuration location.
type GitConfigOptions struct {
	// Path is the repository path, needed for local scope.
	Path string
	// Scope defaults to GitConfigScopeGlobal.
	Scope GitConfigScope
}

// GetConfig returns nil when key is unset (distinct from an empty string).
func (g *Git) GetConfig(ctx context.Context, key string, opts *GitConfigOptions) (*string, error) {
	options, err := gitConfigOptions(opts)
	if err != nil {
		return nil, err
	}
	query := url.Values{"key": {key}, "scope": {string(options.Scope)}}
	if options.Path != "" {
		query.Set("path", options.Path)
	}
	object, err := g.object(ctx, toolboxRequest{Path: "/git/config", Query: query})
	if err != nil {
		return nil, err
	}
	value, err := field[*string](object, "value", false)
	return value, err
}

// SetConfig writes a configuration value. A sandbox access token requires both
// toolbox:git and toolbox:exec (toolbox covers both). Without toolbox:exec it
// returns ErrForbidden, although GetConfig still works.
func (g *Git) SetConfig(ctx context.Context, key, value string, opts *GitConfigOptions) error {
	options, err := gitConfigOptions(opts)
	if err != nil {
		return err
	}
	scope := string(options.Scope)
	body := wire.SetConfigRequest{Key: &key, Value: &value, Path: gitOptional(options.Path), Scope: &scope}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{Method: http.MethodPost, Path: "/git/config", Body: body})
}

func gitConfigOptions(opts *GitConfigOptions) (GitConfigOptions, error) {
	options := GitConfigOptions{Scope: GitConfigScopeGlobal}
	if opts != nil {
		options = *opts
		if options.Scope == "" {
			options.Scope = GitConfigScopeGlobal
		}
	}
	switch options.Scope {
	case GitConfigScopeGlobal, GitConfigScopeLocal:
		return options, nil
	default:
		return GitConfigOptions{}, badRequest("scope must be global or local")
	}
}
