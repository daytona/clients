// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

type waitTestSource struct {
	events   chan stateObservation
	released atomic.Int32
	region   string
}

func (s *waitTestSource) watch(_ context.Context, _, region string) (<-chan stateObservation, func()) {
	s.region = region
	var once sync.Once
	return s.events, func() { once.Do(func() { s.released.Add(1) }) }
}

// Recorder-backed HTTP keeps timers within synctest's virtual clock. Unlike
// real sockets, a handler waiting on the request context is durably blocked.
type waitTestTransport struct{ handler http.HandlerFunc }

func (tr waitTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	response := httptest.NewRecorder()
	tr.handler(response, request)
	if request.Context().Err() != nil {
		return nil, request.Context().Err()
	}
	return response.Result(), nil
}

func virtualWaitSandbox(t *testing.T, handler http.HandlerFunc) *Sandbox {
	t.Helper()
	server := &httptest.Server{URL: "http://wait.example"}
	client := newTestClient(t, server, WithHTTPClient(&http.Client{Transport: waitTestTransport{handler: handler}}))
	sandbox, err := client.lifecycleSandbox("sbx")
	require.NoError(t, err)
	return sandbox
}
