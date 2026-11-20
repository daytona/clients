// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestErrorResponse_whenBodyHasSurroundingWhitespace(t *testing.T) {
	for _, test := range []struct {
		name, mediaType, body, message string
	}{
		{"text", "text/plain", " \n missing \t\r\n", "missing"},
		{"whitespace only", "text/plain", " \t\r\n", "HTTP 404"},
		{"JSON prose", "application/json", "{\"message\":\"  missing \\n\"}", "  missing \n"},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			response := &http.Response{StatusCode: 404, Header: http.Header{"Content-Type": {test.mediaType}}}
			// When
			err := errorFromResponse(response, []byte(test.body))
			// Then
			require.ErrorIs(t, err, ErrNotFound)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, test.message, detail.Message)
		})
	}
}
