// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// apiRequest is the common HTTP request description. Body/Result are typed
// wire structs at callers; any is confined to encoding/json's boundary.
// BodyReader is one-shot; BodyFactory enables replay after a 401.
type apiRequest struct {
	Method           string
	Path             string
	Query            url.Values
	Body             any
	Result           any
	Headers          http.Header
	IdempotencyKey   string
	Timeout          time.Duration
	MaxResponseBytes int64
	Transfer         bool
	BodyReader       io.Reader
	BodyFactory      func(context.Context) (io.ReadCloser, error)
	ResponseBody     *[]byte
	ResponseHeaders  *http.Header
}

type (
	apiClient      struct{ transport }
	toolboxRequest = apiRequest
	transport      struct {
		owner   *Client
		baseURL string
		auth    credentialProvider
	}
)

func (t *transport) do(ctx context.Context, spec apiRequest) (failure error) {
	response, err := t.open(ctx, spec, false)
	if err != nil {
		return err
	}
	defer closeResponse(ctx, response, &failure)
	body, err := t.readResponse(ctx, response, spec.MaxResponseBytes)
	if err != nil {
		return err
	}
	if spec.ResponseBody != nil {
		*spec.ResponseBody = body
	}
	if spec.ResponseHeaders != nil {
		*spec.ResponseHeaders = response.Header.Clone()
	}
	if !isJSONMediaType(response.Header.Get("Content-Type")) || len(body) == 0 {
		if spec.Result != nil {
			return badRequest("expected JSON response: the body is not application/json")
		}
		t.learnSandboxEvents(spec, response, body)
		return nil
	}
	if checkErr := checkJSON(body); checkErr != nil {
		return checkErr
	}
	if spec.Result == nil {
		t.learnSandboxEvents(spec, response, body)
		return nil
	}
	if decodeErr := json.Unmarshal(body, spec.Result); decodeErr != nil {
		return wrapJSONError(decodeErr)
	}
	t.learnSandboxEvents(spec, response, body)
	return nil
}

func wrapJSONError(err error) *Error {
	result := badRequest("expected JSON response: " + err.Error())
	result.Err = err
	return result
}

// stream transfers body ownership to the caller. Close the returned body.
func (t *transport) stream(ctx context.Context, spec apiRequest) (*http.Response, error) {
	return t.open(ctx, spec, true)
}

func relativeURL(base, path string, query url.Values) (string, error) {
	if _, err := parseHTTPBase(base); err != nil {
		return "", err
	}
	parsed, err := url.Parse(path)
	if err != nil || parsed.IsAbs() || parsed.Host != "" || strings.HasPrefix(path, "//") ||
		strings.Contains(path, "\\") || parsed.Fragment != "" {
		return "", badRequest("absolute request URLs are not allowed; pass a relative SDK path")
	}
	endpoint, err := url.Parse(strings.TrimRight(base, "/") + "/" + strings.TrimLeft(path, "/"))
	if err != nil || endpoint.Host == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") {
		return "", badRequest("invalid HTTP endpoint")
	}
	params := endpoint.Query()
	for key, values := range query {
		params[key] = values
	}
	endpoint.RawQuery = params.Encode()
	return endpoint.String(), nil
}

func parseHTTPBase(value string) (*url.URL, error) {
	parsed, err := url.Parse(value)
	if err != nil || parsed.Host == "" || parsed.Hostname() == "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Opaque != "" ||
		parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(value, "#") {
		return nil, badRequest("HTTP base URL must use http or https with a host and no query or fragment")
	}
	return parsed, nil
}

func (t *transport) makeRequest(ctx context.Context, spec apiRequest) (*http.Request, error) {
	endpoint, err := relativeURL(t.baseURL, spec.Path, spec.Query)
	if err != nil {
		return nil, err
	}
	body := spec.BodyReader
	if spec.Body != nil {
		encoded, encodeErr := json.Marshal(spec.Body)
		if encodeErr != nil {
			return nil, wrapJSONError(encodeErr)
		}
		body = bytes.NewReader(encoded)
	}
	var factoryBody io.ReadCloser
	if spec.BodyFactory != nil {
		factoryBody, err = spec.BodyFactory(ctx)
		if err != nil {
			return nil, transportError(ctx, err)
		}
		body = factoryBody
	}
	method := spec.Method
	if method == "" {
		method = http.MethodGet
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		if factoryBody != nil {
			err = errorsWithClose(err, factoryBody)
		}
		return nil, wrapError(err, "invalid HTTP request")
	}
	request.Header = spec.Headers.Clone()
	if request.Header == nil {
		request.Header = make(http.Header)
	}
	request.Header.Set("X-Daytona-Source", "sdk-go")
	request.Header.Set("X-Daytona-SDK-Version", t.owner.version)
	request.Header.Set("User-Agent", "sdk-go/"+t.owner.version)
	if spec.Body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if spec.IdempotencyKey != "" {
		request.Header.Set("Idempotency-Key", spec.IdempotencyKey)
	}
	return request, nil
}
