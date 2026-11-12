// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestAttachWebSocket_preservesServerError_whenUpgradeRefused(t *testing.T) {
	for _, status := range []int{401, 403, 404, 409, 500} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			// Given
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("x-daytona-trace-id", "trace-header")
				w.WriteHeader(status)
				_, _ = io.WriteString(w, `{"code":"PROCESS_NOT_FOUND","message":"missing process","source":"DAYTONA_DAEMON","traceId":"trace-body"}`)
			}))
			defer srv.Close()
			// When
			_, err := newToolboxClient(srv).AttachWebSocket(t.Context(), "missing", "")
			// Then
			details := cerr.DetailsOf(err)
			require.Equal(t, cerr.CodeForStatus(status), details.ExitCode)
			require.Equal(t, "PROCESS_NOT_FOUND", details.Code)
			require.Equal(t, "DAYTONA_DAEMON", details.Source)
			require.Equal(t, "trace-header", details.TraceID)
			require.Contains(t, details.Message, "missing process")
		})
	}
}
