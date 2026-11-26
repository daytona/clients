// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestParseComputerUseStatus_whenGivenABodyDecodesItAsStatusDoes(t *testing.T) {
	cases := []struct{ name, body string }{
		{"not started", `{"status":"inactive","desktop":"not_started"}`},
		{"starting, explicit null cause", `{"status":"inactive","desktop":"starting","cause":null}`},
		{"ready, with a future field", `{"status":"active","desktop":"ready","futureField":{"a":[1]}}`},
		{"failed", `{"status":"inactive","desktop":"failed","cause":{"code":"addon_crashed","message":"m","detail":1}}`},
		{"failed, unknown code", `{"status":"inactive","desktop":"failed","cause":{"code":"future_code","message":"m"}}`},
		{"an unknown pair", `{"status":"paused","desktop":"dimmed","cause":{"code":"x","message":"m"}}`},
		{"failed without cause", `{"status":"inactive","desktop":"failed"}`},
		{"ready with a cause", `{"status":"active","desktop":"ready","cause":{"code":"desktop_exited","message":"m"}}`},
		{"a non-string status", `{"status":1,"desktop":"ready"}`},
		{"not an object", `["inactive"]`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if _, err := w.Write([]byte(tc.body)); err != nil {
					t.Errorf("write fake body: %v", err)
				}
			})
			want, wantErr := sandbox.ComputerUse().Status(t.Context())
			// When
			got, err := ParseComputerUseStatus([]byte(tc.body))
			// Then
			require.Equal(t, want, got)
			if wantErr == nil {
				require.NoError(t, err)
				return
			}
			var wantSDK, gotSDK *Error
			require.ErrorAs(t, wantErr, &wantSDK)
			require.ErrorAs(t, err, &gotSDK)
			require.Equal(t, wantSDK.Message, gotSDK.Message)
			require.Equal(t, [3]any{400, "BAD_REQUEST", "SDK"}, [3]any{gotSDK.StatusCode, gotSDK.Code, gotSDK.Source})
		})
	}
}

func TestParseComputerUseStatus_whenBodyIsNotJSONReturnsBadRequest(t *testing.T) {
	for _, body := range [][]byte{[]byte("not json"), {'"', 0xff, '"'}, nil} {
		// When
		got, err := ParseComputerUseStatus(body)
		// Then
		require.Nil(t, got)
		var sdkErr *Error
		require.ErrorAs(t, err, &sdkErr)
		require.Equal(t, [3]any{400, "BAD_REQUEST", "SDK"}, [3]any{sdkErr.StatusCode, sdkErr.Code, sdkErr.Source})
		require.Equal(t, "expected JSON response: the body is not valid JSON", sdkErr.Message)
	}
}
