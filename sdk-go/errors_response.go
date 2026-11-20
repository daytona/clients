// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"strings"
	"time"
)

// errorFromResponse parses only an exact application/json envelope, retaining
// status on unreadable bodies. It never closes response.Body; its caller owns it.
func errorFromResponse(response *http.Response, body []byte) error {
	result := &Error{StatusCode: response.StatusCode, Message: "HTTP " + strconv.Itoa(response.StatusCode)}
	if response.StatusCode >= 300 && response.StatusCode < 400 {
		result.Source = SourceSDK
		result.Message = "unexpected redirect (the SDK does not follow redirects)"
		return result
	}
	result.RequestID = response.Header.Get("X-Request-ID")
	result.TraceID = response.Header.Get("X-Trace-ID")
	result.RetryAfter = parseRetryAfter(response.Header.Get("Retry-After"))
	if len(body) == 0 {
		return result
	}
	text := strings.ToValidUTF8(string(body), "\uFFFD")
	if !isJSONMediaType(response.Header.Get("Content-Type")) {
		if message := strings.TrimSpace(text); message != "" {
			result.Message = message
		}
		return result
	}
	result.Message = text
	if err := checkJSON(body); err != nil {
		if strings.Contains(err.Error(), "nested too deeply") {
			result.Message = "HTTP " + strconv.Itoa(response.StatusCode) +
				": the response body was nested too deeply to read (over 600 levels of JSON)"
		}
		return result
	}
	object, err := parseObject(body, "error")
	if err != nil {
		return result
	}
	result.Message = "HTTP " + strconv.Itoa(response.StatusCode)
	if message, parseErr := field[string](object, "message", true); parseErr == nil {
		result.Message = message
	} else if prose, proseErr := field[string](object, "error", true); proseErr == nil {
		result.Message = prose
	}
	result.Code = optionalErrorString(object, "code")
	result.Source = optionalErrorString(object, "source")
	populateErrorDetails(result, object)
	if result.Code == CodeImageBuildFailed && result.Source == SourceAPI {
		return &ImageBuildFailedError{SDKError: result}
	}
	return result
}

func populateErrorDetails(result *Error, object jsonObject) {
	if trace := optionalErrorString(object, "trace_id"); trace != "" {
		result.TraceID = trace
	}
	if request := optionalErrorString(object, "requestId"); request != "" {
		result.RequestID = request
	}
	result.Details.Feature = optionalErrorString(object, "feature")
	result.Details.Entitlement = optionalErrorString(object, "entitlement")
	result.Details.Replacement = optionalErrorString(object, "replacement")
	result.Details.FirstAvailableCursor = optionalErrorString(object, "firstAvailableCursor")
	result.Details.StatedRegion = optionalErrorString(object, "stated")
	result.Details.ActualRegion = optionalErrorString(object, "actual")
	result.Details.UnknownRegion = optionalErrorString(object, "region")
	result.Details.LimitName = optionalErrorString(object, "limit")
	result.Details.LimitCurrent = optionalErrorInteger(object, "current")
	result.Details.LimitMax = optionalErrorInteger(object, "max")
	result.Details.MaxBytes = optionalErrorInteger(object, "max_bytes")
	result.Details.Bytes = optionalErrorInteger(object, "bytes")
	if seconds := optionalErrorInteger(object, "retry_after_seconds"); seconds != nil && *seconds >= 0 {
		result.RetryAfter = saturatedDuration(*seconds, time.Second)
	}
	if ms := optionalErrorInteger(object, "retryAfterMs"); ms != nil && *ms >= 0 {
		result.RetryAfter = saturatedDuration(*ms, time.Millisecond)
	}
}

func saturatedDuration(value int64, unit time.Duration) time.Duration {
	const maxDuration = time.Duration(1<<63 - 1)
	if value > int64(maxDuration/unit) {
		return maxDuration
	}
	if value < -int64(maxDuration/unit) {
		return -maxDuration
	}
	return time.Duration(value) * unit
}

func parseRetryAfter(value string) time.Duration {
	if seconds, err := strconv.ParseInt(value, 10, 64); err == nil && seconds >= 0 {
		return saturatedDuration(seconds, time.Second)
	}
	if when, err := http.ParseTime(value); err == nil {
		return max(0, time.Until(when))
	}
	return 0
}
