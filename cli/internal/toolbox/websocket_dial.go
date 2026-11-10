// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"crypto/rand"
	"crypto/sha1"
	"crypto/tls"
	"encoding/base64"
	"fmt"
	"net"
	"net/url"
	"time"
)

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
