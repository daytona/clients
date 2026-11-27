// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"maps"
	"net/http"
	"strings"
)

// UpdateEnvOptions selects SDK-managed environment variables to remove.
type UpdateEnvOptions struct {
	// Unset removes these SDK-managed entries; image environment is unchanged.
	Unset []string
}

// UpdateEnv updates the extra environment inherited by subsequently started
// processes, PTYs and sessions. Nil env sets nothing; nil options unsets nothing.
// Empty names, names containing '=', NUL, CR or LF, and values containing NUL
// return ErrBadRequest before I/O. Platform-reserved names are checked server-side.
func (s *Sandbox) UpdateEnv(ctx context.Context, env map[string]string, opts *UpdateEnvOptions) error {
	values := make(map[string]string, len(env))
	maps.Copy(values, env)
	unset := []string{}
	if opts != nil {
		unset = append(unset, opts.Unset...)
	}
	for key, value := range values {
		if err := sandboxEnvKey(key); err != nil {
			return err
		}
		if err := sandboxEnvValue(value); err != nil {
			return err
		}
	}
	for _, key := range unset {
		if err := sandboxEnvKey(key); err != nil {
			return err
		}
	}
	return s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/env",
		Body: struct {
			Env   map[string]string `json:"env"`
			Unset []string          `json:"unset"`
		}{values, unset},
	})
}

func sandboxEnvValue(value string) error {
	if strings.ContainsRune(value, '\x00') {
		return badRequest("env values must not contain NUL")
	}
	return nil
}

func sandboxEnvKey(key string) error {
	if key == "" || strings.ContainsAny(key, "=\x00\r\n") {
		return badRequest("env key is not a valid variable name")
	}
	return nil
}
