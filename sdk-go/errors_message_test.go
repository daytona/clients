// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestErrorString_whenMessageDuplicatesTag(t *testing.T) {
	for _, tc := range []struct {
		name string
		err  *Error
		want string
	}{
		{"empty", &Error{StatusCode: 404}, "daytona: HTTP 404"},
		{"http fallback", &Error{StatusCode: 404, Message: "HTTP 404"}, "daytona: HTTP 404"},
		{"http with code", &Error{StatusCode: 404, Code: "NOT_FOUND", Message: "HTTP 404"}, "daytona: NOT_FOUND, HTTP 404"},
		{"code fallback", &Error{StatusCode: 404, Code: "NOT_FOUND", Message: "NOT_FOUND"}, "daytona: NOT_FOUND, HTTP 404"},
		{"combined fallback", &Error{StatusCode: 404, Code: "NOT_FOUND", Message: "NOT_FOUND, HTTP 404"}, "daytona: NOT_FOUND, HTTP 404"},
		{"server text", &Error{StatusCode: 404, Message: "HTTP 404 from upstream"}, "daytona: HTTP 404 from upstream (HTTP 404)"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			err := tc.err
			// When
			message := err.Error()
			// Then
			require.Equal(t, tc.want, message)
		})
	}
}

func TestErrorString_whenServerOmitsMessage(t *testing.T) {
	for _, body := range []string{"", `{}`, `{"message":""}`} {
		t.Run(body, func(t *testing.T) {
			// Given
			response := &http.Response{StatusCode: 404, Header: http.Header{"Content-Type": {"application/json"}}}
			// When
			err := errorFromResponse(response, []byte(body))
			// Then
			require.EqualError(t, err, "daytona: HTTP 404")
		})
	}
}

func TestErrorFromResponse_whenEnvelopeOrBodyHasNoUsableProse(t *testing.T) {
	for _, tc := range []struct {
		name, contentType, body, want string
	}{
		{"empty message", "application/json", `{"message":""}`, "HTTP 400"},
		{"empty message and error", "application/json", `{"message":"","error":"bad input"}`, "bad input"},
		{"empty message and empty error", "application/json", `{"message":"","error":""}`, "HTTP 400"},
		{"blank JSON body", "application/json", "  \n", "HTTP 400"},
		{"JSON null", "application/json", "null", "null"},
		{"blank text body", "text/plain", "  ", "HTTP 400"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			response := &http.Response{StatusCode: 400, Header: http.Header{"Content-Type": {tc.contentType}}}
			// When
			err := errorFromResponse(response, []byte(tc.body))
			// Then
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, tc.want, sdkErr.Message)
		})
	}
}
