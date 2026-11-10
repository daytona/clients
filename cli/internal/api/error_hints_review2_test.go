// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImageNotFoundHint_targetsOnlyCreate_whenPostingToSandboxRoutes(t *testing.T) {
	for _, tc := range []struct {
		path   string
		create bool
	}{
		{"/sandboxes", true}, {"/sandboxes/from-snapshot", true}, {"/sandboxes/macos", true},
		{"/sandboxes/sbx/images", false}, {"/sandboxes/sbx/snapshots", false}, {"/sandboxes-other", false},
	} {
		t.Run(tc.path, func(t *testing.T) {
			// Given an image-not-found error from this operation.
			failure := &APIError{Status: http.StatusNotFound, Code: CodeImageNotFound, Method: http.MethodPost, Path: tc.path}
			// When selecting recovery advice.
			hint := hintFor(failure)
			// Then only sandbox creation suggests importing an image.
			if tc.create {
				require.Contains(t, hint, "daytona image create")
			} else {
				require.NotContains(t, hint, "daytona image create")
			}
		})
	}
}
