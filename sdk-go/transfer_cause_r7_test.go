// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

type transferCancelCauseBody struct {
	ctx    context.Context
	cancel context.CancelCauseFunc
	cause  error
}

func (b transferCancelCauseBody) Read([]byte) (int, error) {
	b.cancel(b.cause)
	return 0, b.ctx.Err()
}

func (transferCancelCauseBody) Close() error { return nil }

func TestFileReadsPreserveCause_whenCancelledDuringBody(t *testing.T) {
	for _, operation := range []string{"ReadFile", "Download"} {
		t.Run(operation, func(t *testing.T) {
			// Given a response that cancels the caller while reading its body.
			ctx, cancel := context.WithCancelCause(t.Context())
			defer cancel(nil)
			cause := errors.New("transfer budget withdrawn")
			sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: http.StatusOK, Header: make(http.Header), ContentLength: -1, Request: r,
					Body: transferCancelCauseBody{ctx: r.Context(), cancel: cancel, cause: cause},
				}, nil
			})
			// When reading through either file download surface.
			var err error
			switch operation {
			case "ReadFile":
				_, err = sandbox.FS().ReadFile(ctx, "/file", nil)
			case "Download":
				err = sandbox.FS().Download(ctx, "/file", io.Discard, nil)
			}
			// Then the transfer category, context sentinel and custom cause all survive.
			require.ErrorIs(t, err, cause)
			require.ErrorIs(t, err, context.Canceled)
			require.ErrorIs(t, err, ErrTransferCancelled)
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, 499, sdkErr.StatusCode)
			require.Equal(t, SourceSDK, sdkErr.Source)
		})
	}
}

func TestUploadFilePreservesCause_whenCancelledDuringUpload(t *testing.T) {
	// Given a local source and cancellation as soon as file bytes move.
	sandbox := dockerTimingSandbox(t, func(r *http.Request) (*http.Response, error) {
		_, err := io.Copy(io.Discard, r.Body)
		return nil, errors.Join(err, r.Body.Close(), r.Context().Err())
	})
	path := filepath.Join(t.TempDir(), "source")
	require.NoError(t, os.WriteFile(path, []byte("payload"), 0o600))
	ctx, cancel := context.WithCancelCause(t.Context())
	defer cancel(nil)
	cause := errors.New("upload budget withdrawn")
	// When uploading the local file.
	err := sandbox.FS().UploadFile(ctx, path, "/file", &UploadOptions{
		OnProgress: func(p TransferProgress) {
			if p.Bytes > 0 {
				cancel(cause)
			}
		},
	})
	// Then cancellation retains the same typed transfer error and both causes.
	require.ErrorIs(t, err, cause)
	require.ErrorIs(t, err, context.Canceled)
	require.ErrorIs(t, err, ErrTransferCancelled)
	var sdkErr *Error
	require.ErrorAs(t, err, &sdkErr)
	require.Equal(t, 499, sdkErr.StatusCode)
}
