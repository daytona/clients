// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"errors"
	"fmt"
	"html/template"
	"net"
	"net/http"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

// LoopbackResult is what the redirect handler captured.
type LoopbackResult struct {
	Code  string
	State string
	Err   string
}

// Loopback is a one-shot HTTP listener on 127.0.0.1 for the PKCE redirect.
type Loopback struct {
	ln     net.Listener
	srv    *http.Server
	result chan LoopbackResult
	// Notice, when set, receives one line for a callback that was REFUSED
	// (state missing or mismatched) so the terminal is not left silent while
	// the listener keeps waiting for a verifiable response.
	Notice func(line string)
}

// ListenLoopback binds an ephemeral 127.0.0.1 port.
func ListenLoopback(expectedState string) (*Loopback, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, cerr.Wrap(cerr.ExitFailure, err, "cannot open a loopback port for the browser redirect").
			WithHint("use `daytona login --device` instead")
	}
	lb := &Loopback{ln: ln, result: make(chan LoopbackResult, 1)}
	mux := http.NewServeMux()
	mux.HandleFunc("/callback", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		res := LoopbackResult{Code: q.Get("code"), State: q.Get("state"), Err: q.Get("error")}
		stateMismatch := strings.TrimSpace(expectedState) != "" && strings.TrimSpace(res.State) != expectedState
		if res.Err != "" {
			if d := q.Get("error_description"); d != "" {
				res.Err += ": " + d
			}
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		if stateMismatch {
			// Refused, not delivered: without the state we cannot tell this
			// response from one a local process forged to abort or spoof the
			// login (RFC 6749 section 4.1.2.1 requires `state` on error redirects
			// too). Say so in the browser AND the terminal, then keep waiting.
			w.WriteHeader(http.StatusBadRequest)
			why := "the response carried no valid state, so it cannot be verified"
			if res.Err != "" {
				why += "; the provider reported: " + res.Err
			}
			_ = loginFailedPage.Execute(w, struct{ Err string }{Err: why + ". Return to the terminal and retry the login from the CLI"})
			if lb.Notice != nil {
				lb.Notice("ignored a browser callback without a valid state (" + why + "); still waiting — press Ctrl-C to abort")
			}
			return
		}
		if res.Err == "" && res.Code == "" {
			// Neither a code nor an error: a malformed or stray request. Make
			// the failure explicit so the CLI never posts an empty code.
			res.Err = "the authorization response carried neither a code nor an error"
		}
		if res.Err != "" {
			w.WriteHeader(http.StatusBadRequest)
			// html/template contextually escapes the provider-supplied error.
			_ = loginFailedPage.Execute(w, struct{ Err string }{Err: res.Err})
		} else {
			// Neutral: the code still has to be exchanged by the CLI, which
			// may yet fail — the terminal has the final word.
			fmt.Fprint(w, "<html><body><h2>Authorization response received</h2><p>Return to the terminal to finish signing in. You can close this tab.</p></body></html>")
		}
		select {
		case lb.result <- res:
		default:
		}
	})
	lb.srv = &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = lb.srv.Serve(ln) }()
	return lb, nil
}

// RedirectURI is the registered loopback redirect for this listener.
func (l *Loopback) RedirectURI() string {
	port := 0
	if addr, ok := l.ln.Addr().(*net.TCPAddr); ok {
		port = addr.Port
	}
	return fmt.Sprintf("http://127.0.0.1:%d/callback", port)
}

// Wait blocks for the redirect or ctx/timeout.
func (l *Loopback) Wait(ctx context.Context, timeout time.Duration) (LoopbackResult, error) {
	defer l.Close()
	select {
	case r := <-l.result:
		return r, nil
	case <-time.After(timeout):
		return LoopbackResult{}, cerr.New(cerr.ExitTimeout, "timed out waiting for the browser login").
			WithHint("run `daytona login` again, or `daytona login --device` on a remote host")
	case <-ctx.Done():
		return LoopbackResult{}, cerr.New(cerr.ExitInterrupted, "interrupted")
	}
}

// Close stops the listener.
func (l *Loopback) Close() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_ = l.srv.Shutdown(ctx)
}

// OpenBrowser tries the platform opener; returns an error the caller may
// ignore (it prints the URL as a fallback). It is a variable so tests can
// replace it — the default forks the OS opener, which a test must never do.
var OpenBrowser = openBrowser

func openBrowser(u string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", u)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", u)
	default:
		if _, err := exec.LookPath("xdg-open"); err != nil {
			return errors.New("xdg-open not found")
		}
		cmd = exec.Command("xdg-open", u)
	}
	return cmd.Start()
}

var loginFailedPage = template.Must(template.New("login-failed").Parse(
	"<html><body><h2>Login failed</h2><p>{{.Err}}</p><p>You can close this tab.</p></body></html>"))
