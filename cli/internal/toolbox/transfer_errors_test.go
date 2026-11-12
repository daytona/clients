// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSignedTransfers_keepParseReason_whenURLIsInvalid(t *testing.T) {
	for _, upload := range []bool{false, true} {
		t.Run(fmt.Sprint(upload), func(t *testing.T) {
			// Given a signed URL whose path contains a forbidden control byte.
			url := "https://proxy.example/files\x7f?signature=private"
			// When building the transfer request.
			var err error
			if upload {
				_, _, err = newSignedUploadRequest(t.Context(), url, "local", "remote", strings.NewReader("x"))
			} else {
				err = transferClient().DownloadFile(t.Context(), url, filepath.Join(t.TempDir(), "out"))
			}
			// Then the reason survives without disclosing the URL or signature.
			require.ErrorContains(t, err, "invalid control character")
			require.NotContains(t, err.Error(), "proxy.example")
			require.NotContains(t, err.Error(), "signature")
		})
	}
}

type review2ErrorReader struct{ err error }

func (r review2ErrorReader) Read([]byte) (int, error) { return 0, r.err }

func TestSignedUpload_preservesReadContext_whenReaderFails(t *testing.T) {
	for _, pathError := range []bool{false, true} {
		t.Run(fmt.Sprint(pathError), func(t *testing.T) {
			// Given a source with useful nested error context.
			cause := errors.New("device offline")
			var sourceErr error = fmt.Errorf("decode source: %w", cause)
			if pathError {
				sourceErr = &os.PathError{Op: "read", Path: "local.txt", Err: cause}
			}
			// When streaming its multipart body.
			body, _, done := startSignedUploadBody("local.txt", "remote", review2ErrorReader{sourceErr})
			defer body.Close()
			_, err := io.Copy(io.Discard, body)
			// Then error identity and useful context survive without a repeated path.
			require.ErrorIs(t, err, cause)
			require.Equal(t, 1, strings.Count(err.Error(), "local.txt"))
			if !pathError {
				require.ErrorContains(t, err, "decode source")
			}
			require.ErrorIs(t, <-done, sourceErr)
		})
	}
}
