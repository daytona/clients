// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestImageLogs_refusesMalformedIdentity_beforeDiscovery(t *testing.T) {
	// Given an image response missing its required id.
	requests := make(chan string, 4)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests <- r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{}`)
	}))
	defer srv.Close()
	// When reading its logs.
	out, _, code := run(t, cfgDir(t), nil, "--api-url", srv.URL, "--api-key", "fixture", "image", "logs", "name")
	// Then only the image lookup occurs, and no partial output is printed.
	require.Equal(t, cerr.ExitFailure, code)
	require.Empty(t, out)
	require.Len(t, requests, 1)
}

func TestImageLogs_refusesOversizedBody_whenNotFollowing(t *testing.T) {
	// Given a build log larger than the in-memory read budget.
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/images/name":
			_ = json.NewEncoder(w).Encode(map[string]string{"id": "img-a"})
		case "/images/img-a/build-logs-url":
			_ = json.NewEncoder(w).Encode(map[string]string{"url": srv.URL + "/build-logs/images/img-a"})
		case "/build-logs/images/img-a":
			chunk := bytes.Repeat([]byte("x"), 64<<10)
			for range 1025 {
				if _, err := w.Write(chunk); err != nil {
					return
				}
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	// When reading the logs without --follow.
	out, stderr, code := run(t, cfgDir(t), nil, "--api-url", srv.URL, "--api-key", "fixture", "image", "logs", "name")
	// Then the bounded read fails without printing a truncated success.
	require.Equal(t, cerr.ExitFailure, code)
	require.Empty(t, out)
	require.Contains(t, stderr, "--follow")
}
