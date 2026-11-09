// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

type Client struct {
	inner *api.Client
	// OnLogWarning, when set, receives each `warning` event of a followed
	// log stream (an expired cursor: frames were lost and the stream resumed
	// from FirstAvailableCursor). Commands print it so a gap never passes
	// for a quiet process.
	OnLogWarning func(LogWarning)
}

type daemonErrorEnvelope struct {
	tbgen.ErrorEnvelope
	FirstAvailableCursor *string `json:"firstAvailableCursor,omitempty"`
}

func New(c *api.Client) *Client {
	return &Client{inner: c}
}

func (c *Client) BaseURL() string {
	return c.inner.BaseURL
}

func (c *Client) Credential() string {
	return c.inner.Credential
}

func (c *Client) GetJSON(ctx context.Context, path string, query url.Values, out any) error {
	resp, err := c.do(ctx, http.MethodGet, path, query, nil, "", false)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return decodeJSON(resp.Body, out)
}

func (c *Client) PostJSON(ctx context.Context, path string, query url.Values, in, out any) error {
	var body io.Reader
	if in != nil {
		payload, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("encode request: %w", err)
		}
		body = bytes.NewReader(payload)
	}
	resp, err := c.do(ctx, http.MethodPost, path, query, body, "application/json", false)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if out == nil || resp.StatusCode == http.StatusNoContent {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	return decodeJSON(resp.Body, out)
}

func (c *Client) Delete(ctx context.Context, path string) error {
	resp, err := c.do(ctx, http.MethodDelete, path, nil, nil, "", false)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

func (c *Client) Stream(ctx context.Context, method, path string, query url.Values, body io.Reader, contentType string) (*http.Response, error) {
	return c.do(ctx, method, path, query, body, contentType, true)
}

func (c *Client) do(ctx context.Context, method, path string, query url.Values, body io.Reader, contentType string, noClientTimeout bool) (*http.Response, error) {
	reqURL := c.inner.BaseURL + path
	if len(query) > 0 {
		reqURL += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, reqURL, body)
	if err != nil {
		return nil, fmt.Errorf("build %s %s request: %w", method, path, err)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	req.Header.Set("Accept", "application/json")
	c.inner.SetIdentityHeaders(req)
	if c.inner.Credential != "" {
		req.Header.Set("Authorization", "Bearer "+c.inner.Credential)
	}
	if c.inner.Debug != nil {
		c.inner.Debug("> %s %s", method, reqURL)
	}
	httpClient := c.inner.HTTP
	if noClientTimeout && httpClient != nil {
		clone := *httpClient
		clone.Timeout = 0
		httpClient = &clone
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, cerr.New(cerr.ExitInterrupted, "interrupted")
		}
		if isTimeoutError(err) {
			return nil, cerr.Wrap(cerr.ExitTimeout, err, "%s %s: request timed out", method, path)
		}
		return nil, cerr.Wrap(cerr.ExitFailure, err, "%s %s: %v", method, path, err).
			WithHint("check the toolbox proxy URL (%s) and your network", c.inner.BaseURL)
	}
	if c.inner.Debug != nil {
		c.inner.Debug("< %d", resp.StatusCode)
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp, nil
	}
	defer resp.Body.Close()
	return nil, mapError(method, path, resp)
}

func decodeJSON(r io.Reader, out any) error {
	if out == nil {
		_, _ = io.Copy(io.Discard, r)
		return nil
	}
	if err := json.NewDecoder(r).Decode(out); err != nil {
		return fmt.Errorf("decode JSON response: %w", err)
	}
	return nil
}

func mapError(method, path string, resp *http.Response) error {
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var env daemonErrorEnvelope
	_ = json.Unmarshal(body, &env)
	message := strings.TrimSpace(env.Message)
	if message == "" {
		message = strings.TrimSpace(string(body))
	}
	if message == "" {
		message = http.StatusText(resp.StatusCode)
	}
	if code := strings.TrimSpace(env.Code); code != "" && code != message && !strings.Contains(message, code) {
		message = fmt.Sprintf("%s (%s)", message, code)
	}
	e := cerr.New(cerr.CodeForStatus(resp.StatusCode), "%s %s: %d %s", method, path, resp.StatusCode, message)
	if hint := hintFor(resp.StatusCode, env); hint != "" {
		e = e.WithHint("%s", hint)
	}
	traceID := resp.Header.Get("x-daytona-trace-id")
	if env.TraceId != nil && traceID == "" {
		traceID = *env.TraceId
	}
	// Always attached: the error came from the server, even without an
	// envelope, so it is never reported as the CLI's own.
	e.Err = &DaemonError{Code: strings.TrimSpace(env.Code), Status: resp.StatusCode, Source: strings.TrimSpace(env.Source), TraceID: traceID}
	return e
}

// DaemonError carries the toolbox API's machine-readable error code
// (x-daytona-error-codes in the toolbox contract), source and trace id.
type DaemonError struct {
	Code    string
	Status  int
	Source  string
	TraceID string
}

func (d *DaemonError) Error() string {
	if d.Code != "" {
		return d.Code
	}
	return fmt.Sprintf("%d %s", d.Status, http.StatusText(d.Status))
}

// ErrorCode, ErrorSource and ErrorTraceID implement cerr.Detailed.
func (d *DaemonError) ErrorCode() string    { return d.Code }
func (d *DaemonError) ErrorSource() string  { return d.Source }
func (d *DaemonError) ErrorTraceID() string { return d.TraceID }

// IsDaemonCode reports whether err carries the given toolbox error code.
func IsDaemonCode(err error, code string) bool {
	var de *DaemonError
	return errors.As(err, &de) && de.Code == code
}

func hintFor(status int, env daemonErrorEnvelope) string {
	switch status {
	case http.StatusUnauthorized:
		return "credential rejected — toolbox calls need a project credential for this sandbox"
	case http.StatusForbidden:
		return "this credential is not allowed to do that on the sandbox"
	case http.StatusNotFound:
		return "check the sandbox id, process id, or file path"
	case http.StatusConflict:
		switch env.Code {
		case api.CodeSandboxNotRunning:
			return "the sandbox is not running — start the sandbox and retry"
		case api.CodeCursorExpired:
			if env.FirstAvailableCursor != nil && strings.TrimSpace(*env.FirstAvailableCursor) != "" {
				return fmt.Sprintf("the requested log cursor expired — resume from cursor %s", *env.FirstAvailableCursor)
			}
			return "the requested log cursor expired — restart from the earliest retained output"
		}
		return "the sandbox or process is not in a state that allows that operation"
	}
	return ""
}

func isTimeoutError(err error) bool {
	type timeout interface{ Timeout() bool }
	te, ok := err.(timeout)
	return ok && te.Timeout()
}

func durationToMilliseconds(d time.Duration) int64 {
	return d.Milliseconds()
}
