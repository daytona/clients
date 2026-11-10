// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package toolbox

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
)

func TestForwardTerminalResizes_sendsInitialGeometry_withoutSignal(t *testing.T) {
	// Given
	sizes := make(chan tbgen.ResizeRequest, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/processes/proc/resize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var size tbgen.ResizeRequest
		if err := json.NewDecoder(r.Body).Decode(&size); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		sizes <- size
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	client := newToolboxClient(srv)
	want := terminalSize(int(os.Stdout.Fd()))
	// When
	stop := client.forwardTerminalResizes(t.Context(), "proc")
	defer stop()
	// Then
	select {
	case size := <-sizes:
		require.Equal(t, tbgen.ResizeRequest{Cols: want.Cols, Rows: want.Rows}, size)
	case <-time.After(2 * time.Second):
		t.Fatal("attachment did not send the current geometry before SIGWINCH")
	}
}
