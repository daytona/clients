// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package api is the handwritten HTTP transport over the generated wire types
// (gen/api). Credentials are opaque bearers: the client
// sends `Authorization: Bearer <credential>` and maps the server's answer to
// the CLI's exit-code vocabulary; it never inspects the token.
package api

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

	"github.com/daytona/clients/cli/internal/cerr"
)

// Source is the X-Daytona-Source attribution value. Together with
// X-Daytona-SDK-Version and User-Agent it is the client-identity triple every
// Daytona client sends so usage per client and version
// can be tracked server-side.
const Source = "cli"

// Client talks to one API base URL with one credential.
type Client struct {
	BaseURL    string
	Credential string // opaque bearer; empty = unauthenticated
	HTTP       *http.Client
	UserAgent  string
	Version    string // sent as X-Daytona-SDK-Version
	// Debug, when set, receives one line per request/response.
	Debug func(format string, a ...any)
	// OnUnauthorized, when set, is consulted ONCE per client after a 401: it
	// may return a replacement bearer (e.g. a project key minted with the
	// current credential), and the request is
	// retried once with it. Returning "" keeps the original 401.
	OnUnauthorized func(ctx context.Context) (string, error)
	// RetryForbidden widens the OnUnauthorized trigger to 403: set when the
	// Credential is a CACHED exchanged key, which may have been minted under
	// a since-widened ceiling (or revoked) — the exchange then re-evaluates
	// against the server instead of failing until the cache entry expires.
	RetryForbidden bool
	exchanged      bool
}

// New builds a Client with sane timeouts.
func New(baseURL, credential, version string) *Client {
	return &Client{
		BaseURL:    strings.TrimRight(baseURL, "/"),
		Credential: credential,
		HTTP:       &http.Client{Timeout: 60 * time.Second},
		UserAgent:  Source + "/" + version,
		Version:    version,
	}
}

// SetIdentityHeaders stamps the client-identity triple onto req.
func (c *Client) SetIdentityHeaders(req *http.Request) {
	req.Header.Set("User-Agent", c.UserAgent)
	req.Header.Set("X-Daytona-Source", Source)
	req.Header.Set("X-Daytona-SDK-Version", c.Version)
}

// WithCredential returns a shallow copy using another bearer.
func (c *Client) WithCredential(cred string) *Client {
	cp := *c
	cp.Credential = cred
	return &cp
}

// WithBaseURL returns a shallow copy targeting another regional API base URL.
func (c *Client) WithBaseURL(base string) *Client {
	cp := *c
	cp.BaseURL = strings.TrimRight(base, "/")
	return &cp
}

type APIError struct {
	Status  int
	Code    string
	Message string
	Source  string
	TraceID string
	Body    map[string]any
	Method  string
	Path    string
}

// ErrorCode, ErrorSource and ErrorTraceID expose the server's error
// envelope (cerr.Detailed).
func (e *APIError) ErrorCode() string    { return e.Code }
func (e *APIError) ErrorSource() string  { return e.Source }
func (e *APIError) ErrorTraceID() string { return e.TraceID }

func (e *APIError) Error() string {
	msg := strings.TrimSpace(e.Message)
	if msg == "" && e.Code != "" {
		msg = e.Code
	}
	if msg == "" {
		msg = http.StatusText(e.Status)
	} else if e.Code != "" && e.Code != msg {
		msg = fmt.Sprintf("%s (%s)", msg, e.Code)
	}
	return fmt.Sprintf("%s %s: %d %s", e.Method, e.Path, e.Status, msg)
}

// Do performs a JSON request. `in` (may be nil) is marshalled as the body,
// `out` (may be nil) receives the decoded 2xx body. Non-2xx returns *APIError
// wrapped in a *cerr.Error with the mapped exit code and a hint.
func (c *Client) Do(ctx context.Context, method, path string, query url.Values, in, out any) error {
	return c.DoWithHeaders(ctx, method, path, query, nil, in, out)
}

func (c *Client) DoWithHeaders(ctx context.Context, method, path string, query url.Values, headers http.Header, in, out any) error {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("encode request: %w", err)
		}
		body = bytes.NewReader(b)
	}
	resp, err := c.raw(ctx, method, path, query, headers, body, "application/json", false)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if out == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	if resp.StatusCode == http.StatusNoContent {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("decode %s %s response: %w", method, path, err)
	}
	return nil
}

// Stream performs a request and returns the open response for the caller to
// consume (logs, SSE). The caller closes it. Non-2xx is mapped like Do.
func (c *Client) Stream(ctx context.Context, method, path string, query url.Values, in io.Reader, contentType string) (*http.Response, error) {
	return c.raw(ctx, method, path, query, nil, in, contentType, false)
}

// StreamNoTimeout is Stream without the HTTP client's overall timeout: for
// long-lived follow streams ONLY (a build's log tail runs as long as the
// build), where the caller's context is the bound. Everything else keeps the
// timeout so a stalled server cannot hang the CLI.
func (c *Client) StreamNoTimeout(ctx context.Context, method, path string, query url.Values, in io.Reader, contentType string) (*http.Response, error) {
	return c.raw(ctx, method, path, query, nil, in, contentType, true)
}

func (c *Client) raw(ctx context.Context, method, path string, query url.Values, headers http.Header, body io.Reader, contentType string, noClientTimeout bool) (*http.Response, error) {
	u := c.BaseURL + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, u, body)
	if err != nil {
		return nil, err
	}
	if body != nil && contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	req.Header.Set("Accept", "application/json")
	c.SetIdentityHeaders(req)
	if c.Credential != "" {
		req.Header.Set("Authorization", "Bearer "+c.Credential)
	}
	for name, values := range headers {
		for _, value := range values {
			req.Header.Add(name, value)
		}
	}
	if c.Debug != nil {
		c.Debug("> %s %s", method, u)
	}
	httpClient := c.HTTP
	if noClientTimeout && httpClient != nil {
		clone := *httpClient
		clone.Timeout = 0
		httpClient = &clone
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		var ue *url.Error
		if errors.As(err, &ue) && ue.Timeout() {
			return nil, cerr.Wrap(cerr.ExitTimeout, err, "%s %s: request timed out", method, path)
		}
		if ctx.Err() != nil {
			return nil, cerr.Wrap(cerr.ExitInterrupted, err, "interrupted")
		}
		return nil, cerr.Wrap(cerr.ExitFailure, err, "%s %s: %v", method, path, err).
			WithHint("check the API URL (%s) and your network", c.BaseURL)
	}
	if c.Debug != nil {
		c.Debug("< %d %s", resp.StatusCode, resp.Header.Get("x-daytona-trace-id"))
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp, nil
	}
	rejected := resp.StatusCode == http.StatusUnauthorized || (c.RetryForbidden && resp.StatusCode == http.StatusForbidden)
	if rejected && c.OnUnauthorized != nil && !c.exchanged && c.Credential != "" {
		// One bounded exchange: the caller may know how to turn this
		// credential into one the route admits (a fresh login-minted project
		// key). JSON bodies are *bytes.Reader and rewind for the retry; a
		// streaming body (Stream) cannot be replayed, so only those stay
		// one-shot. The original answer, with the server's error envelope,
		// stands when no replacement is available.
		c.exchanged = true
		original := c.mapError(method, path, resp)
		resp.Body.Close()
		replay := body == nil
		if seeker, ok := body.(io.Seeker); ok && !replay {
			_, serr := seeker.Seek(0, io.SeekStart)
			replay = serr == nil
		}
		if replay {
			if cred, err := c.OnUnauthorized(ctx); err == nil && cred != "" && cred != c.Credential {
				if c.Debug != nil {
					c.Debug("~ %d → retrying once with an exchanged credential", resp.StatusCode)
				}
				c.Credential = cred
				c.RetryForbidden = false // the fresh key is authoritative
				return c.raw(ctx, method, path, query, headers, body, contentType, noClientTimeout)
			}
		}
		return nil, original
	}
	defer resp.Body.Close()
	return nil, c.mapError(method, path, resp)
}

func (c *Client) mapError(method, path string, resp *http.Response) error {
	ae := &APIError{Status: resp.StatusCode, Method: method, Path: path, TraceID: resp.Header.Get("x-daytona-trace-id")}
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if json.Unmarshal(raw, &ae.Body) == nil {
		if s, ok := ae.Body["code"].(string); ok {
			ae.Code = s
		}
		if s, ok := ae.Body["message"].(string); ok {
			ae.Message = s
		}
		if s, ok := ae.Body["source"].(string); ok {
			ae.Source = s
		}
		if s, ok := ae.Body["trace_id"].(string); ok && ae.TraceID == "" {
			ae.TraceID = s
		}
	} else if len(raw) > 0 {
		ae.Message = strings.TrimSpace(string(raw))
	}
	code := cerr.CodeForStatus(resp.StatusCode)
	if ae.Status == http.StatusTooManyRequests && ae.Code == CodeLimitExceeded {
		// An organization limit requires a configuration or usage change,
		// not a retry, so report a conflict instead of a transient failure.
		code = cerr.ExitConflict
	}
	e := cerr.Wrap(code, ae, "%s", ae.Error())
	e.Hint = hintFor(ae)
	return e
}
