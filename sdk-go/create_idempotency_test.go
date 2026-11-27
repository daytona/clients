// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreateIdempotencyNamesRoundTripDeterministically(t *testing.T) {
	for _, name := range []string{"worker", strings.Repeat("x", 251), strings.Repeat("x", 252), " café ", "name\tcontrol"} {
		t.Run(name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx"})})
			client := newTestClient(t, fake.Server)
			expected := "name:" + name
			if name != "worker" && name != strings.Repeat("x", 251) {
				digest := sha256.Sum256([]byte(name))
				expected = "name:sha256:" + hex.EncodeToString(digest[:])
			}
			// When
			_, err := client.Create(t.Context(), "", &CreateOptions{Wait: NoWait, Name: name})
			// Then
			require.NoError(t, err)
			request := fake.requests()[0]
			require.Equal(t, expected, request.Header.Get("Idempotency-Key"))
			var body struct {
				Key    *string           `json:"idempotency_key"`
				Name   string            `json:"name"`
				Labels map[string]string `json:"labels"`
			}
			require.NoError(t, json.Unmarshal(request.Body, &body))
			require.Nil(t, body.Key, "the key is sent as the header only")
			require.Equal(t, name, body.Name)
			require.Equal(t, name, body.Labels["name"])
		})
	}
}

func TestCreateIdempotencyExplicitKeyWins(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx"})})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Create(t.Context(), "", &CreateOptions{Wait: NoWait, Name: "named", IdempotencyKey: "explicit"})
	// Then
	require.NoError(t, err)
	require.Equal(t, "explicit", fake.requests()[0].Header.Get("Idempotency-Key"))
}

func TestCreateIdempotencyUnnamedCallsHaveIndependentKeys(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx"})})
	client := newTestClient(t, fake.Server)
	// When
	_, firstErr := client.Create(t.Context(), "", &CreateOptions{Wait: NoWait})
	_, secondErr := client.Create(t.Context(), "", &CreateOptions{Wait: NoWait})
	// Then
	require.NoError(t, firstErr)
	require.NoError(t, secondErr)
	requests := fake.requests()
	require.NotEmpty(t, requests[0].Header.Get("Idempotency-Key"))
	require.NotEqual(t, requests[0].Header.Get("Idempotency-Key"), requests[1].Header.Get("Idempotency-Key"))
}

func TestCreateIdempotencyReconcilesLostResponseWithReportedKey(t *testing.T) {
	// Given
	var attempts atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": func(w http.ResponseWriter, _ *http.Request) {
			if attempts.Add(1) == 1 {
				loseCreateResponse(t, w)
				return
			}
			writeJSON(t, w, map[string]string{"id": "original", "status": "running"})
		},
	})
	client := newTestClient(t, fake.Server)
	// When
	_, firstErr := client.Create(t.Context(), "img_source", &CreateOptions{Wait: NoWait})
	var uncertain *SandboxCreateConnectionError
	require.ErrorAs(t, firstErr, &uncertain)
	reconciled, retryErr := client.Create(t.Context(), "img_source", &CreateOptions{
		Wait: NoWait, IdempotencyKey: uncertain.IdempotencyKey,
	})
	// Then
	require.NoError(t, retryErr)
	require.Equal(t, "original", reconciled.ID())
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.Equal(t, requests[0].Header.Get("Idempotency-Key"), requests[1].Header.Get("Idempotency-Key"))
	require.JSONEq(t, string(requests[0].Body), string(requests[1].Body))
}
