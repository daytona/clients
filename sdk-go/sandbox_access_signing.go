// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// RotateSigningKey invalidates previously signed file URLs within approximately
// 30 seconds. The key is never returned. ErrUnsupportedOperation identifies a
// caller-supplied direct toolbox URL that is not scoped to this sandbox.
// A closed owning client returns ErrClientClosed before scope validation.
func (s *Sandbox) RotateSigningKey(ctx context.Context) error {
	if err := s.client.ensureOpen(); err != nil {
		return err
	}
	segment, err := pathSegment(s.id)
	if err != nil {
		return err
	}
	s.mu.RLock()
	scoped := s.toolboxScoped
	s.mu.RUnlock()
	if !scoped {
		return newSDKError(CodeUnsupportedOperation, 400,
			"signing key rotation requires a toolbox URL scoped to the sandbox")
	}
	return s.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/signing-key/rotate",
	})
}
