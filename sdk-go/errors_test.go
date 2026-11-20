// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestErrors_whenSourceAndCodeDisagreeWithHTTPStatus(t *testing.T) {
	for _, test := range []struct {
		source, code string
		status       int
		want         error
	}{
		{SourceDaemon, "FILE_NOT_FOUND", 500, ErrNotFound},
		{"FUTURE_SOURCE", "FILE_NOT_FOUND", 500, ErrInternalServer},
		{SourceAPI, "FILE_NOT_FOUND", 409, ErrConflict},
		{"FUTURE_SOURCE", "NOT_FOUND", 500, ErrNotFound},
		{SourceAPI, "BILLING_DISABLED", 500, ErrNotSupported},
		{SourceProxy, "SANDBOX_NOT_RUNNING", 500, ErrConflict},
		{SourceRouter, "REGION_MISMATCH", 500, ErrBadRequest},
		{SourceAPI, "FUTURE_CODE", 429, ErrRateLimit},
		{SourceSDK, "FILE_NOT_FOUND", 500, ErrNotFound},
	} {
		t.Run(test.source+"/"+test.code, func(t *testing.T) {
			// Given
			response := &http.Response{StatusCode: test.status, Header: http.Header{"Content-Type": {"application/json"}}}
			body, err := json.Marshal(map[string]string{"code": test.code, "source": test.source, "message": "failed"})
			require.NoError(t, err)
			// When
			err = errorFromResponse(response, body)
			// Then
			require.ErrorIs(t, err, test.want)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, test.status, detail.StatusCode)
			require.Equal(t, test.code, detail.Code)
		})
	}
}

func TestErrors_whenMatchingCodeSentinels(t *testing.T) {
	// Given
	cause := &Error{StatusCode: 404, Source: SourceDaemon, Code: CodeFileNotFound, Message: "missing"}
	// When/Then
	require.ErrorIs(t, cause, ErrFileNotFound)
	require.ErrorIs(t, ErrFileNotFound, ErrNotFound)
	require.ErrorIs(t, ErrSandboxNotRunning, ErrConflict)
	require.ErrorIs(t, ErrBillingDisabled, ErrInternalServer)
	require.NotErrorIs(t, cause, ErrGitRepoNotFound)
}

func TestErrors_whenEnvelopeCarriesDetails(t *testing.T) {
	// Given
	response := &http.Response{StatusCode: 429, Header: http.Header{
		"Content-Type": {"Application/JSON; charset=utf-8"}, "Retry-After": {"9"},
	}}
	body := []byte(`{"code":"LIMIT_EXCEEDED","source":"DAYTONA_API","message":"quota",
		"trace_id":"trace","requestId":"request","limit":"sandboxes","current":5,"max":6,
		"max_bytes":100,"bytes":200,"retryAfterMs":1250,"feature":"f","entitlement":"e",
		"replacement":"new","firstAvailableCursor":"oldest","stated":"a","actual":"b","region":"c"}`)
	// When
	err := errorFromResponse(response, body)
	// Then
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.ErrorIs(t, err, ErrLimitExceeded)
	require.Equal(t, "trace", detail.TraceID)
	require.Equal(t, "request", detail.RequestID)
	require.Equal(t, 1250*time.Millisecond, detail.RetryAfter)
	require.EqualValues(t, 5, *detail.Current)
	require.EqualValues(t, 6, *detail.Max)
	require.EqualValues(t, 100, *detail.MaxBytes)
	require.Equal(t, "oldest", detail.FirstAvailableCursor)
}

func TestErrors_whenMediaTypeIsNotJSON(t *testing.T) {
	// Given
	body := []byte(`{"code":"FILE_NOT_FOUND","source":"DAYTONA_DAEMON","message":"missing"}`)
	response := &http.Response{StatusCode: 404, Header: http.Header{"Content-Type": {"application/problem+json"}}}
	// When
	err := errorFromResponse(response, body)
	// Then
	require.ErrorIs(t, err, ErrNotFound)
	require.NotErrorIs(t, err, ErrFileNotFound)
	require.Equal(t, string(body), err.Error())
}

func TestCatalog_whenReferenceAddsCode(t *testing.T) {
	// Given: a drift guard on the sibling's contract-checked catalog, not copied expectations.
	source, err := os.ReadFile("../sdk-typescript/src/v2/codes.ts")
	require.NoError(t, err)
	catalogs := regexp.MustCompile(`(?s)export const (\w+)_ERROR_CODES = Object.freeze\(\[(.*?)\] as const\)`)
	codes := regexp.MustCompile(`'([A-Z][A-Z0-9_]+)'`)
	statuses := regexp.MustCompile(`(?m)^  ([A-Z][A-Z0-9_]+): (\d+),`)
	statusByCode := make(map[string]int)
	for _, match := range statuses.FindAllStringSubmatch(string(source), -1) {
		status, parseErr := strconv.Atoi(match[2])
		require.NoError(t, parseErr)
		statusByCode[match[1]] = status
	}
	for _, catalog := range catalogs.FindAllStringSubmatch(string(source), -1) {
		for _, code := range codes.FindAllStringSubmatch(catalog[2], -1) {
			// When
			got := catalogStatus("DAYTONA_"+catalog[1], code[1])
			// Then
			require.Equal(t, statusByCode[code[1]], got, "%s/%s", catalog[1], code[1])
		}
	}
}

func TestErrors_whenJSONDepthExceedsLimit(t *testing.T) {
	// Given
	body := []byte(`{"code":"FILE_NOT_FOUND","details":` + strings.Repeat("[", 600) + strings.Repeat("]", 600) + `}`)
	response := &http.Response{StatusCode: 404, Header: http.Header{"Content-Type": {"application/json"}}}
	// When
	err := errorFromResponse(response, body)
	// Then
	require.ErrorIs(t, err, ErrNotFound)
	require.NotErrorIs(t, err, ErrFileNotFound)
	require.Contains(t, err.Error(), "nested too deeply")
}
