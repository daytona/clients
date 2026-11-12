// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

func (c *Client) CreateProcess(ctx context.Context, body tbgen.CreateProcessRequest, query url.Values) (tbgen.ProcessDto, error) {
	var out tbgen.ProcessDto
	if err := c.PostJSON(ctx, "/processes", query, body, &out); err != nil {
		return tbgen.ProcessDto{}, err
	}
	return normalizeProcess(out), nil
}

// GetProcess reads one process; the result keeps the toolbox's JSON.
func (c *Client) GetProcess(ctx context.Context, id string) (api.Raw[tbgen.ProcessDto], error) {
	var out api.Raw[tbgen.ProcessDto]
	if err := c.GetJSON(ctx, "/processes/"+url.PathEscape(id), nil, &out); err != nil {
		return api.Raw[tbgen.ProcessDto]{}, err
	}
	out.V = normalizeProcess(out.V)
	return out, nil
}

// ListProcesses lists processes; each item keeps the toolbox's JSON.
func (c *Client) ListProcesses(ctx context.Context, query url.Values) ([]api.Raw[tbgen.ProcessDto], error) {
	var body json.RawMessage
	if err := c.GetJSON(ctx, "/processes", query, &body); err != nil {
		return nil, err
	}
	page, err := api.DecodePage[tbgen.ProcessDto](body, "items")
	if err != nil {
		return nil, fmt.Errorf("decode JSON response: %w", err)
	}
	for i := range page.Items {
		page.Items[i].V = normalizeProcess(page.Items[i].V)
	}
	return page.Items, nil
}

// WaitProcess waits for a process to end; the result keeps the toolbox's JSON.
func (c *Client) WaitProcess(ctx context.Context, id string, timeout time.Duration) (api.Raw[tbgen.ExitResult], error) {
	if timeout <= 0 {
		for {
			result, terminal, err := c.waitOnce(ctx, id, 50*time.Second)
			if err != nil {
				return api.Raw[tbgen.ExitResult]{}, err
			}
			if terminal {
				return result, nil
			}
		}
	}
	deadline := time.Now().Add(timeout)
	for {
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return api.Raw[tbgen.ExitResult]{}, cerr.New(cerr.ExitTimeout, "timed out after %s waiting for process %s", timeout, id)
		}
		step := remaining
		if step > 50*time.Second {
			step = 50 * time.Second
		}
		result, terminal, err := c.waitOnce(ctx, id, step)
		if err != nil {
			return api.Raw[tbgen.ExitResult]{}, err
		}
		if terminal {
			return result, nil
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

// ReadProcessLogs reads one page of a process's logs; it keeps the
// toolbox's JSON.
func (c *Client) ReadProcessLogs(ctx context.Context, id string, query url.Values) (api.Raw[tbgen.ProcessLogsResponse], error) {
	var out api.Raw[tbgen.ProcessLogsResponse]
	if err := c.GetJSON(ctx, "/processes/"+url.PathEscape(id)+"/logs", query, &out); err != nil {
		return api.Raw[tbgen.ProcessLogsResponse]{}, err
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
		encoded := base64.URLEncoding.EncodeToString(data)
		body.Data = &encoded
		encoding := "base64"
		body.Encoding = &encoding
	}
	return c.PostJSON(ctx, "/processes/"+url.PathEscape(id)+"/stdin", nil, body, nil)
}

// ResizeProcess changes the terminal geometry of a PTY process.
func (c *Client) ResizeProcess(ctx context.Context, id string, size tbgen.TerminalSize) error {
	return c.PostJSON(ctx, "/processes/"+url.PathEscape(id)+"/resize", nil, tbgen.ResizeRequest{Cols: size.Cols, Rows: size.Rows}, nil)
}

// InterruptProcess is best-effort cleanup after the caller's signal, not
// after a client wait deadline. The server owns escalation, so it survives
// this CLI exiting. Ignore cleanup failures: preserve the interruption.
func (c *Client) InterruptProcess(ctx context.Context, id string) {
	if id == "" {
		return
	}
	cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	if err := c.SignalProcess(cleanupCtx, id, "INT", time.Second, "KILL"); err != nil {
		return
	}
}

func (c *Client) ListPorts(ctx context.Context) ([]int32, error) {
	var out tbgen.PortList
	if err := c.GetJSON(ctx, "/port", nil, &out); err != nil {
		return nil, err
	}
	return out.Ports, nil
}

func (c *Client) waitOnce(ctx context.Context, id string, step time.Duration) (api.Raw[tbgen.ExitResult], bool, error) {
	query := url.Values{}
	query.Set("timeoutMs", fmt.Sprintf("%d", step.Milliseconds()))
	var out api.Raw[tbgen.ExitResult]
	if err := c.GetJSON(ctx, "/processes/"+url.PathEscape(id)+"/wait", query, &out); err != nil {
		return api.Raw[tbgen.ExitResult]{}, false, err
	}
	out.V = normalizeExit(out.V)
	if out.V.Reason == nil {
		return out, true, nil
	}
	switch *out.V.Reason {
	case tbgen.ReasonTimedOut:
		// Disambiguate this overloaded wire reason below.
	default:
		return out, true, nil
	}
	// The same reason denotes an expired long poll and an execution deadline.
	// Read the record to avoid waiting forever for a process already stopped
	// by --execution-timeout; it can also have exited since the poll ended.
	proc, err := c.GetProcess(ctx, id)
	if err != nil {
		return out, false, err
	}
	switch proc.V.State {
	case tbgen.StateTerminal:
		return api.Raw[tbgen.ExitResult]{V: resultFromProcess(proc.V)}, true, nil
	default:
		return out, false, nil
	}
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
