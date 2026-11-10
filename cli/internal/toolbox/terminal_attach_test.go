// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAttachTerminal_waitsForProcess_onlyAfterCloseFrame(t *testing.T) {
	// Given
	stdin, err := os.Open(os.DevNull)
	require.NoError(t, err)
	defer stdin.Close()
	previousStdin := os.Stdin
	os.Stdin = stdin
	defer func() { os.Stdin = previousStdin }()
	for _, cleanClose := range []bool{false, true} {
		t.Run(map[bool]string{false: "transport drop", true: "close frame"}[cleanClose], func(t *testing.T) {
			var waits atomic.Int32
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/processes/proc/attach":
					conn, rw, err := w.(http.Hijacker).Hijack()
					if err != nil {
						return
					}
					defer conn.Close()
					_, _ = fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", wsAccept(r.Header.Get("Sec-WebSocket-Key")))
					_ = rw.Flush()
					if cleanClose {
						writeServerFrame(conn, 0x8, nil)
					}
				case "/processes/proc/wait":
					waits.Add(1)
					_, _ = io.WriteString(w, `{"reason":"exited","exitCode":0}`)
				case "/processes/proc/resize":
					w.WriteHeader(http.StatusNoContent)
				default:
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer srv.Close()
			client := newToolboxClient(srv)
			// When
			_, err := client.AttachTerminal(t.Context(), ProcessAttachment{ID: "proc"})
			// Then
			if cleanClose {
				require.NoError(t, err)
				require.EqualValues(t, 1, waits.Load())
			} else {
				require.ErrorIs(t, err, io.ErrUnexpectedEOF)
				require.Zero(t, waits.Load())
			}
		})
	}
}
