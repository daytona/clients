// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/url"
	"strings"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/cerr"
)

func (c *Client) CreateProcess(ctx context.Context, body tbgen.CreateProcessRequest, query url.Values) (tbgen.ProcessDto, error) {
	var out tbgen.ProcessDto
	if err := c.PostJSON(ctx, "/processes", query, body, &out); err != nil {
		return tbgen.ProcessDto{}, err
	}
	return normalizeProcess(out), nil
}

func (c *Client) GetProcess(ctx context.Context, id string) (tbgen.ProcessDto, error) {
	var out tbgen.ProcessDto
	if err := c.GetJSON(ctx, "/processes/"+url.PathEscape(id), nil, &out); err != nil {
		return tbgen.ProcessDto{}, err
	}
	return normalizeProcess(out), nil
}

func (c *Client) ListProcesses(ctx context.Context, query url.Values) ([]tbgen.ProcessDto, error) {
	var out tbgen.ProcessListResponse
	if err := c.GetJSON(ctx, "/processes", query, &out); err != nil {
		return nil, err
	}
	items := make([]tbgen.ProcessDto, 0, len(out.Items))
	for _, item := range out.Items {
		items = append(items, normalizeProcess(item))
	}
	return items, nil
}

func (c *Client) WaitProcess(ctx context.Context, id string, timeout time.Duration) (tbgen.ExitResult, error) {
	if timeout <= 0 {
		for {
			result, err := c.waitOnce(ctx, id, 50*time.Second)
			if err != nil {
				return tbgen.ExitResult{}, err
			}
			if result.Reason == nil || *result.Reason != tbgen.ReasonTimedOut {
				return normalizeExit(result), nil
			}
		}
	}
	deadline := time.Now().Add(timeout)
	for {
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return tbgen.ExitResult{}, cerr.New(cerr.ExitTimeout, "timed out after %s waiting for process %s", timeout, id)
		}
		step := remaining
		if step > 50*time.Second {
			step = 50 * time.Second
		}
		result, err := c.waitOnce(ctx, id, step)
		if err != nil {
			return tbgen.ExitResult{}, err
		}
		if result.Reason == nil || *result.Reason != tbgen.ReasonTimedOut {
			return normalizeExit(result), nil
		}
	}
}

func (c *Client) SignalProcess(ctx context.Context, id, signal string, escalateAfter time.Duration, escalateTo string) error {
	body := tbgen.SignalRequest{Signal: stringPtr(toWireSignal(signal))}
	if escalateAfter > 0 {
		ms := durationToMilliseconds(escalateAfter)
		body.EscalateAfterMs = &ms
	}
	if strings.TrimSpace(escalateTo) != "" {
		body.EscalateTo = stringPtr(toWireSignal(escalateTo))
	}
	return c.PostJSON(ctx, "/processes/"+url.PathEscape(id)+"/signals", nil, body, nil)
}

func (c *Client) ReadProcessLogs(ctx context.Context, id string, query url.Values) (tbgen.ProcessLogsResponse, error) {
	var out tbgen.ProcessLogsResponse
	if err := c.GetJSON(ctx, "/processes/"+url.PathEscape(id)+"/logs", query, &out); err != nil {
		return tbgen.ProcessLogsResponse{}, err
	}
	return out, nil
}

func (c *Client) AttachWebSocket(ctx context.Context, processID string, cursor string) (*WebSocketConn, error) {
	attachURL := c.BaseURL() + "/processes/" + url.PathEscape(processID) + "/attach"
	values := url.Values{}
	if cursor != "" {
		values.Set("cursor", cursor)
	}
	if token := strings.TrimSpace(c.Credential()); token != "" {
		values.Set("daytona_token", token)
	}
	if encoded := values.Encode(); encoded != "" {
		attachURL += "?" + encoded
	}
	return dialWebSocket(ctx, attachURL, c.inner.SetIdentityHeaders)
}

func (c *Client) WriteStdin(ctx context.Context, id string, data []byte, eof bool) error {
	body := tbgen.StdinRequest{Eof: boolPtr(eof)}
	if len(data) > 0 {
		encoded := base64.StdEncoding.EncodeToString(data)
		body.Data = &encoded
		encoding := "base64"
		body.Encoding = &encoding
	}
	return c.PostJSON(ctx, "/processes/"+url.PathEscape(id)+"/stdin", nil, body, nil)
}

func (c *Client) ListPorts(ctx context.Context) ([]int32, error) {
	var out tbgen.PortList
	if err := c.GetJSON(ctx, "/port", nil, &out); err != nil {
		return nil, err
	}
	return out.Ports, nil
}

func (c *Client) waitOnce(ctx context.Context, id string, step time.Duration) (tbgen.ExitResult, error) {
	query := url.Values{}
	query.Set("timeoutMs", fmt.Sprintf("%d", step.Milliseconds()))
	var out tbgen.ExitResult
	if err := c.GetJSON(ctx, "/processes/"+url.PathEscape(id)+"/wait", query, &out); err != nil {
		return tbgen.ExitResult{}, err
	}
	return normalizeExit(out), nil
}

func normalizeProcess(p tbgen.ProcessDto) tbgen.ProcessDto {
	if p.Signal != nil {
		signal := fromWireSignal(*p.Signal)
		p.Signal = &signal
	}
	return p
}

func normalizeExit(r tbgen.ExitResult) tbgen.ExitResult {
	if r.Signal != nil {
		signal := fromWireSignal(*r.Signal)
		r.Signal = &signal
	}
	return r
}

func toWireSignal(signal string) string {
	s := strings.ToUpper(strings.TrimSpace(signal))
	if s == "" {
		return ""
	}
	if strings.HasPrefix(s, "SIG") {
		return s
	}
	return "SIG" + s
}

func fromWireSignal(signal string) string {
	s := strings.ToUpper(strings.TrimSpace(signal))
	return strings.TrimPrefix(s, "SIG")
}

func stringPtr(v string) *string { return &v }
func boolPtr(v bool) *bool       { return &v }
