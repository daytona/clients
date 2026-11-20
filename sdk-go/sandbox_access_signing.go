// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"
	"strings"
)

// RotateSigningKey invalidates previously signed file URLs within approximately
// 30 seconds. The key is never returned. ErrUnsupportedOperation identifies a
// caller-supplied direct toolbox URL that is not scoped to this sandbox.
func (s *Sandbox) RotateSigningKey(ctx context.Context) error {
	segment, err := pathSegment(s.id)
	if err != nil {
		return err
	}
	s.mu.RLock()
	endpoint, direct := s.toolboxURL, s.explicitToolboxURL && !s.toolboxURLFromTemplate
	s.mu.RUnlock()
	if direct {
		path := endpoint
		if parsed, parseErr := url.Parse(endpoint); parseErr == nil {
			path = parsed.EscapedPath()
		}
		path = strings.TrimSuffix(path, "/")
		if !strings.HasSuffix(path, "/"+segment) && !strings.HasSuffix(path, "/"+s.id) {
			return newSDKError(CodeUnsupportedOperation, 400,
				"signing key rotation requires a toolbox URL scoped to the sandbox")
		}
	}
	return s.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/signing-key/rotate",
	})
}
