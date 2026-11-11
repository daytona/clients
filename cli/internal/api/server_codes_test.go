// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// The image-name refusals exit with the documented status mapping and keep
// their code.
func TestImageNameCodes_exitWithTheStatusMapping(t *testing.T) {
	cases := []struct {
		name   string
		status int
		code   string
		body   string
		exit   int
	}{
		{"taken", 409, CodeImageNameTaken, `{"code":"IMAGE_NAME_TAKEN","message":"image name 'py' already exists for different content or import parameters","source":"DAYTONA_API"}`, cerr.ExitConflict},
		{"reserved", 400, CodeImageNameReserved, `{"code":"IMAGE_NAME_RESERVED","message":"image name 'img_x' is reserved: 'img_' is a resource id prefix","source":"DAYTONA_API"}`, cerr.ExitUsage},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			err := New(srv.URL, "", "t").Do(context.Background(), http.MethodPost, "/images", nil, nil, nil)

			require.Equal(t, tc.exit, cerr.ExitCode(err))
			var ae *APIError
			require.True(t, errors.As(err, &ae))
			require.Equal(t, tc.status, ae.Status)
			require.Equal(t, tc.code, ae.Code)
		})
	}
}
