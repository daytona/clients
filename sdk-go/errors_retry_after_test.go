// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestRetryAfter_whenEnvelopeSecondsAreNegative(t *testing.T) {
	for _, tc := range []struct {
		name, header, body string
		want               time.Duration
	}{
		{"negative", "", `{"retry_after_seconds":-1}`, 0},
		{"preserves header", "3", `{"retry_after_seconds":-1}`, 3 * time.Second},
		{"milliseconds wins", "3", `{"retry_after_seconds":-1,"retryAfterMs":250}`, 250 * time.Millisecond},
		{"zero", "3", `{"retry_after_seconds":0}`, 0},
		{"positive", "3", `{"retry_after_seconds":2}`, 2 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			response := &http.Response{StatusCode: 429, Header: http.Header{
				"Content-Type": {"application/json"}, "Retry-After": {tc.header},
			}}
			// When
			err := errorFromResponse(response, []byte(tc.body))
			// Then
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, tc.want, detail.RetryAfter)
		})
	}
}
