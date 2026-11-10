// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestWriteStdin_usesContractBase64URL_whenBytesAreBinary(t *testing.T) {
	// Given
	payload := []byte{0xfb, 0xff, 0xfe, 0, 1}
	var body tbgen.StdinRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.NoError(t, json.NewDecoder(r.Body).Decode(&body))
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	// When
	err := newToolboxClient(srv).WriteStdin(t.Context(), "proc", payload, false)
	// Then
	require.NoError(t, err)
	require.NotNil(t, body.Data)
	decoded, err := base64.URLEncoding.DecodeString(*body.Data)
	require.NoError(t, err)
	require.Equal(t, payload, decoded)
}
