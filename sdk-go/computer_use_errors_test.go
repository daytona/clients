// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestComputerUse_whenMalformedStatusRejected(t *testing.T) {
	for _, body := range []string{
		`null`, `[]`, `"inactive"`, `{}`, `{"status":123,"desktop":"ready"}`,
		`{"status":"active","desktop":true}`, `{"status":"inactive","desktop":"failed"}`,
		`{"status":"inactive","desktop":"failed","cause":null}`,
		`{"status":"inactive","desktop":"rebooting","cause":"crashed"}`,
		`{"status":"inactive","desktop":"rebooting","cause":{"code":123,"message":"x"}}`,
		`{"status":"inactive","desktop":"failed","cause":{"message":"x"}}`,
		`{"status":"inactive","desktop":"failed","cause":{"code":"start_failed"}}`,
		`{"status":"inactive","desktop":"failed","cause":{"code":"x","message":1}}`,
		`{"status":"active","desktop":"ready","cause":{"code":"x","message":"x"}}`,
		`{"status":"inactive","desktop":"starting","cause":{"code":"x","message":"x"}}`,
		`{"status":"inactive","desktop":"not_started","cause":{"code":"x","message":"x"}}`,
	} {
		t.Run(body, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
			// When
			_, err := sandbox.ComputerUse().Status(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, "SDK", sdkErr.Source)
			require.Equal(t, "BAD_REQUEST", sdkErr.Code)
		})
	}
}

func TestComputerUse_whenServerErrorMapped(t *testing.T) {
	for _, tc := range []struct {
		code   string
		status int
		want   error
	}{
		{"DESKTOP_NOT_READY", 409, ErrDesktopNotReady},
		{"ADDON_UNAVAILABLE", 503, ErrAddonUnavailable},
		{"UNSUPPORTED_OPERATION", 400, ErrUnsupportedOperation},
		{"FUTURE_CODE", 409, ErrConflict},
	} {
		t.Run(tc.code, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tc.status)
				writeJSON(t, w, map[string]string{"source": "DAYTONA_DAEMON", "code": tc.code, "message": "unavailable"})
			})
			// When
			_, err := sandbox.ComputerUse().Start(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, tc.want)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, tc.code, sdkErr.Code)
			require.Equal(t, "DAYTONA_DAEMON", sdkErr.Source)
		})
	}
}

func TestComputerUse_whenInvalidInputMakesNoRequest(t *testing.T) {
	type invalidCase struct {
		name string
		call func(context.Context, *ComputerUse) error
	}
	cases := make([]invalidCase, 0, 8)
	cases = append(cases,
		invalidCase{"negative delay", func(ctx context.Context, c *ComputerUse) error {
			return c.Keyboard().Type(ctx, "x", &ComputerUseTypeOptions{Delay: -time.Second})
		}},
		invalidCase{"negative limit", func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Recordings().Download(ctx, "rec", &ComputerUseRecordingDownloadOptions{MaxResponseBytes: -2})
			return err
		}},
		invalidCase{"unsafe limit", func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Recordings().Download(ctx, "rec", &ComputerUseRecordingDownloadOptions{MaxResponseBytes: maxSafeInteger + 1})
			return err
		}},
	)
	for _, id := range []string{"", " ", ".", "..", string([]byte{0xff})} {
		cases = append(cases,
			invalidCase{"path id " + id, func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Recordings().Get(ctx, id)
				return err
			}})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, map[string]string{}))
			// When
			err := tc.call(t.Context(), sandbox.ComputerUse())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}

func TestComputerUse_whenContextCanceled(t *testing.T) {
	// Given
	entered := make(chan struct{})
	sandbox, _ := computerUseTestSandbox(t, func(_ http.ResponseWriter, r *http.Request) {
		close(entered)
		<-r.Context().Done()
	})
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan error, 1)
	// When
	go func() {
		_, err := sandbox.ComputerUse().Status(ctx)
		done <- err
	}()
	<-entered
	cancel()
	// Then
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestComputerUse_whenClientClosed(t *testing.T) {
	// Given
	sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, map[string]string{}))
	require.NoError(t, sandbox.client.Close())
	// When
	_, err := sandbox.ComputerUse().Status(t.Context())
	// Then
	require.ErrorIs(t, err, ErrClientClosed)
	require.Len(t, fake.requests(), 1)
}
