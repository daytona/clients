// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strings"
	"unicode/utf8"
)

const (
	buildLogMaxEvent    = 128 * 1024
	buildLogMaxSnapshot = 2 * 1024 * 1024
)

var buildLogImageRoute = regexp.MustCompile(`^/images/([^/]+)/build-logs-url$`)

type buildLogTarget struct {
	api  *apiClient
	path string
}

func discoverBuildLogs(ctx context.Context, api *apiClient, path string) (*buildLogTarget, error) {
	discoveryCtx, scope := newRequestScope(ctx, api.owner, api.owner.config.timeout)
	defer scope.close()
	scope.kick()
	target := buildLogTarget{api: api, path: path}
	body, err := target.readFinite(discoveryCtx, apiRequest{Path: path, MaxResponseBytes: buildLogMaxEvent},
		buildLogMaxEvent)
	if err != nil {
		return nil, err
	}
	if jsonErr := checkJSON(body); jsonErr != nil {
		return nil, buildLogProtocol(jsonErr.Error())
	}
	object, err := parseObject(body, "build log discovery")
	if err != nil {
		return nil, buildLogProtocol(err.Error())
	}
	raw, err := field[string](object, "url", true)
	if err != nil {
		return nil, buildLogProtocol(err.Error())
	}
	destination, err := buildLogDestination(api, raw, path, "")
	// The discovery path's segment, already encoded.
	route := buildLogImageRoute.FindStringSubmatch(path)
	if err == nil || !errors.Is(err, errBuildLogIdentity) || route == nil {
		return destination, err
	}
	segment := route[1]
	// Discovery answers with the image's canonical ID, which a name never
	// matches: resolve the value with the API (GET /images/{value}, as every
	// image method resolves names) and hold the URL to that ID just as strictly.
	var current JSONValue
	if err = api.do(discoveryCtx, apiRequest{Path: "/images/" + segment, Result: &current}); err != nil {
		return nil, err
	}
	canonical, err := imageLookupID(current)
	if err != nil {
		return nil, err
	}
	return buildLogDestination(api, raw, path, canonical)
}

// imageLookupID is the ID an image lookup answered with, checked as every
// response ID a request path will carry is (the messages of the other SDKs).
func imageLookupID(current JSONValue) (string, error) {
	object, err := parseObject(current, "image")
	if err != nil {
		return "", badRequest("image response is not a JSON object")
	}
	canonical, err := field[string](object, "id", true)
	if err != nil || strings.TrimSpace(canonical) == "" {
		return "", badRequest("image response is missing its id")
	}
	if err = checkResponseID(canonical); err != nil {
		return "", err
	}
	return canonical, nil
}

func (t buildLogTarget) readFinite(ctx context.Context, spec apiRequest, limit int64) (data []byte, failure error) {
	response, err := t.api.stream(ctx, spec)
	if err != nil {
		return nil, err
	}
	defer closeResponse(ctx, response, &failure)
	if response.ContentLength > limit {
		return nil, buildLogProtocol("build log response exceeds its byte limit")
	}
	data, err = io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, transportError(ctx, err)
	}
	if int64(len(data)) > limit {
		return nil, buildLogProtocol("build log response exceeds its byte limit")
	}
	return data, nil
}

func buildLogProtocol(message string) *Error {
	return newSDKError("BUILD_LOG_PROTOCOL_ERROR", 0, message)
}

func buildLogInterrupt(ctx context.Context, cursor string, cause error) error {
	var interrupted *BuildLogInterruptedError
	if ctx.Err() == nil && errors.As(cause, &interrupted) {
		return interrupted
	}
	code, status := CodeBuildLogInterrupted, 0
	var sdkError *Error
	if errors.As(cause, &sdkError) {
		if sdkError.Code != "" {
			code = sdkError.Code
		}
		status = sdkError.StatusCode
	}
	if ctx.Err() != nil {
		cause = transportError(ctx, context.Cause(ctx))
		switch {
		case errors.Is(cause, ErrClientClosed):
			code, status = CodeClientClosed, 0
		case errors.Is(cause, context.DeadlineExceeded):
			code, status = CodeRequestTimeout, http.StatusRequestTimeout
		default:
			code, status = "BUILD_LOG_CANCELED", 0
		}
	}
	result := newSDKError(code, status, cause.Error())
	result.Err = cause
	return &BuildLogInterruptedError{SDKError: result, Cursor: cursor}
}

func buildLogCursorValid(cursor string) bool {
	if cursor == "" || !utf8.ValidString(cursor) || utf8.RuneCountInString(cursor) > 2048 {
		return false
	}
	for _, char := range cursor {
		if char < 32 || char == 127 {
			return false
		}
	}
	return true
}

func buildLogTextWidth(data []byte) int {
	char, width := utf8.DecodeRune(data)
	if char != utf8.RuneError || width != 1 {
		return width
	}
	// Consume the valid prefix of a malformed multibyte sequence as one
	// replacement, matching UTF-8 maximal-subpart decoding.
	first := data[0]
	if first < 0xc2 || first > 0xf4 {
		return 0
	}
	size := 2
	if first >= 0xe0 {
		size = 3
	}
	if first >= 0xf0 {
		size = 4
	}
	n := 1
	for n < size && n < len(data) {
		next := data[n]
		if next < 0x80 || next > 0xbf {
			break
		}
		if n == 1 && !buildLogUTF8Second(first, next) {
			break
		}
		n++
	}
	return -n
}

func buildLogUTF8Second(first, next byte) bool {
	switch first {
	case 0xe0:
		return next >= 0xa0
	case 0xed:
		return next <= 0x9f
	case 0xf0:
		return next >= 0x90
	case 0xf4:
		return next <= 0x8f
	default:
		return true
	}
}
