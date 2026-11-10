// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/sha1"
	"crypto/tls"
	"encoding/base64"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
)

// dialWebSocket opens a WebSocket; decorate (optional) may add headers to the
// handshake request. Production callers go through Client.AttachWebSocket,
// which passes the client-identity triple — dial from there, not from here.
func dialWebSocket(ctx context.Context, rawURL string, decorate func(*http.Request)) (ws *WebSocketConn, err error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return nil, fmt.Errorf("parse websocket URL: %w", err)
	}
	switch parsed.Scheme {
	case "http":
		parsed.Scheme = "ws"
	case "https":
		parsed.Scheme = "wss"
	case "ws", "wss":
	default:
		return nil, fmt.Errorf("unsupported websocket scheme %q", parsed.Scheme)
	}
	conn, err := dialWebSocketConn(ctx, parsed)
	if err != nil {
		if ctx.Err() != nil {
			return nil, cerr.FromContext(ctx)
		}
		return nil, err
	}
	handshakeCanceled := make(chan struct{})
	stopHandshake := context.AfterFunc(ctx, func() {
		defer close(handshakeCanceled)
		if err := conn.Close(); err != nil {
			return
		}
	})
	defer func() {
		// Stopping an AfterFunc does not join a callback already in flight.
		// Join it before handing ownership of the transport to the caller.
		if !stopHandshake() {
			<-handshakeCanceled
		}
		if ctx.Err() != nil {
			_ = conn.Close()
			ws = nil
			err = cerr.FromContext(ctx)
		}
	}()
	reader := bufio.NewReader(conn)
	key, err := randomWebSocketKey()
	if err != nil {
		_ = conn.Close()
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, parsed.String(), nil)
	if err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("build websocket request: %w", err)
	}
	req.Header.Set("Host", parsed.Host)
	req.Header.Set("Connection", "Upgrade")
	req.Header.Set("Upgrade", "websocket")
	req.Header.Set("Sec-WebSocket-Version", "13")
	req.Header.Set("Sec-WebSocket-Key", key)
	if decorate != nil {
		decorate(req)
	}
	if err := req.Write(conn); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("write websocket handshake: %w", err)
	}
	resp, err := http.ReadResponse(reader, req)
	if err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("read websocket handshake: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusSwitchingProtocols {
		err := mapError(http.MethodGet, parsed.EscapedPath(), resp)
		_ = conn.Close()
		return nil, fmt.Errorf("websocket handshake failed: %w", err)
	}
	if !strings.EqualFold(resp.Header.Get("Upgrade"), "websocket") {
		_ = conn.Close()
		return nil, fmt.Errorf("websocket handshake failed: missing Upgrade: websocket")
	}
	if !strings.EqualFold(resp.Header.Get("Connection"), "Upgrade") {
		_ = conn.Close()
		return nil, fmt.Errorf("websocket handshake failed: missing Connection: Upgrade")
	}
	if resp.Header.Get("Sec-WebSocket-Accept") != expectedWebSocketAccept(key) {
		_ = conn.Close()
		return nil, fmt.Errorf("websocket handshake failed: bad accept key")
	}
	return &WebSocketConn{conn: conn, r: reader}, nil
}

func dialWebSocketConn(ctx context.Context, parsed *url.URL) (net.Conn, error) {
	dialer := &net.Dialer{Timeout: 30 * time.Second}
	host := websocketDialAddress(parsed)
	if parsed.Scheme == "wss" {
		return (&tls.Dialer{NetDialer: dialer, Config: &tls.Config{ServerName: parsed.Hostname()}}).DialContext(ctx, "tcp", host)
	}
	return dialer.DialContext(ctx, "tcp", host)
}

func websocketDialAddress(parsed *url.URL) string {
	if parsed.Port() != "" {
		return parsed.Host
	}
	defaultPort := "80"
	if parsed.Scheme == "wss" {
		defaultPort = "443"
	}
	return net.JoinHostPort(parsed.Hostname(), defaultPort)
}

func randomWebSocketKey() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", fmt.Errorf("generate websocket key: %w", err)
	}
	return base64.StdEncoding.EncodeToString(raw[:]), nil
}

func expectedWebSocketAccept(key string) string {
	sum := sha1.Sum([]byte(key + wsGUID))
	return base64.StdEncoding.EncodeToString(sum[:])
}
