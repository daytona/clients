// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenResponseIDContainsLoneSurrogate_stopsBeforeWait(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes": jsonHandler(t, JSONValue(`{"id":"sbx-\ud800","status":"pending"}`)),
	})
	client := newTestClient(t, fake.Server)
	// When
	sandbox, err := client.Create(t.Context(), "", nil)
	// Then
	require.Nil(t, sandbox)
	require.ErrorIs(t, err, ErrBadRequest)
	require.Len(t, fake.requests(), 1)
}

func TestReplaceSecrets_whenNameInvalidUTF8_sendsNoRequest(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	sandbox := newMutatorSandbox(t, fake)
	// When
	_, err := sandbox.ReplaceSecrets(t.Context(), []string{"valid", string([]byte{0xff})}, &IfMatchOptions{IfMatch: `"rev"`})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}
