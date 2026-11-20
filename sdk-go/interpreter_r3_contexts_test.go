// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestInterpreterR3CreateContext_whenBindingFails_preservesNamedUncertainty(t *testing.T) {
	for _, id := range []string{".", "..", " "} {
		t.Run(id, func(t *testing.T) {
			// Given an accepted named create with a string ID that cannot bind.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sessions": jsonHandler(t, map[string]string{"id": id, "kernel": "python"}),
			})
			service := interpreterTestSandbox(t, fake).Interpreter()
			// When the accepted response cannot be used as a context handle.
			bound, err := service.CreateContext(t.Context(), "python",
				&CreateInterpreterContextOptions{ContextID: "requested"})
			// Then the requested cleanup identity and the original error survive.
			require.Nil(t, bound)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Contains(t, sdkError.Message, `context "requested" may have been created`)
			require.ErrorIs(t, sdkError.Err, ErrBadRequest)
		})
	}
}

func TestInterpreterR3CreateContext_whenLanguageUnsupported_preservesServerError(t *testing.T) {
	for _, language := range []string{"", "future-language"} {
		t.Run(language, func(t *testing.T) {
			// Given the server owns kernel support, including an empty kernel.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sessions": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusBadRequest)
					writeJSON(t, w, map[string]string{
						"code": "UNSUPPORTED_OPERATION", "source": "DAYTONA_DAEMON", "message": "unsupported kernel",
					})
				},
			})
			service := interpreterTestSandbox(t, fake).Interpreter()
			// When creating the requested language.
			_, err := service.CreateContext(t.Context(), language, nil)
			// Then the server error category is not replaced by local BAD_REQUEST.
			require.ErrorIs(t, err, ErrUnsupportedOperation)
			requests := fake.requests()
			require.Len(t, requests, 1)
			var body interpreterContextRequest
			require.NoError(t, json.Unmarshal(requests[0].Body, &body))
			require.Equal(t, language, body.Kernel)
		})
	}
}
