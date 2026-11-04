// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/stretchr/testify/require"
)

// wsEchoServer performs the server side of an RFC 6455 handshake and then
// echoes every text/binary frame back unmasked. Ping frames get a Pong reply.
// Close frames get a close reply.
func wsEchoServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
			http.Error(w, "not a websocket", http.StatusBadRequest)
			return
		}
		key := r.Header.Get("Sec-WebSocket-Key")
		if key == "" {
			http.Error(w, "missing key", http.StatusBadRequest)
			return
		}
		accept := wsAccept(key)
		hj, ok := w.(http.Hijacker)
		if !ok {
			http.Error(w, "hijack unsupported", http.StatusInternalServerError)
			return
		}
		conn, bufrw, err := hj.Hijack()
		if err != nil {
			return
		}
		defer conn.Close()

		// Write the 101 response.
		_, _ = bufrw.WriteString("HTTP/1.1 101 Switching Protocols\r\n")
		_, _ = bufrw.WriteString("Upgrade: websocket\r\n")
		_, _ = bufrw.WriteString("Connection: Upgrade\r\n")
		_, _ = bufrw.WriteString("Sec-WebSocket-Accept: " + accept + "\r\n")
		_, _ = bufrw.WriteString("\r\n")
		_ = bufrw.Flush()

		// Echo loop: read client frames, unmask, echo back unmasked.
		reader := bufio.NewReader(conn)
		for {
			opcode, payload, err := readServerFrame(reader)
			if err != nil {
				return
			}
			switch opcode {
			case 0x8: // close
				writeServerFrame(conn, 0x8, payload)
				return
			case 0x9: // ping
				writeServerFrame(conn, 0xA, payload) // pong
			case 0x1, 0x2: // text, binary
				writeServerFrame(conn, opcode, payload)
			case 0x0: // continuation — echo as-is
				writeServerFrame(conn, 0x0, payload)
			}
		}
	}))
}

// wsSendServerPing sends a server-side ping frame on the raw connection before
// echoing. Used by the ping-pong test.
func wsPingThenEchoServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Sec-WebSocket-Key")
		accept := wsAccept(key)
		hj, ok := w.(http.Hijacker)
		if !ok {
			return
		}
		conn, bufrw, err := hj.Hijack()
		if err != nil {
			return
		}
		defer conn.Close()

		_, _ = bufrw.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n")
		_ = bufrw.Flush()

		// Send a server ping first.
		writeServerFrame(conn, 0x9, []byte("ping-payload"))

		reader := bufio.NewReader(conn)
		for {
			opcode, payload, err := readServerFrame(reader)
			if err != nil {
				return
			}
			switch opcode {
			case 0x8:
				writeServerFrame(conn, 0x8, payload)
				return
			case 0xA: // pong from client — ignore
				continue
			case 0x1, 0x2:
				writeServerFrame(conn, opcode, payload)
			}
		}
	}))
}

func wsAccept(key string) string {
	sum := sha1.Sum([]byte(key + wsGUID))
	return base64.StdEncoding.EncodeToString(sum[:])
}

func serveHandshakeFailure(t *testing.T, response string) (string, <-chan error, func()) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	closed := make(chan error, 1)
	go func() {
		defer close(closed)
		conn, acceptErr := ln.Accept()
		if acceptErr != nil {
			return
		}
		defer conn.Close()
		reader := bufio.NewReader(conn)
		for {
			line, readErr := reader.ReadString('\n')
			if readErr != nil || line == "\r\n" {
				break
			}
		}
		_, _ = io.WriteString(conn, response)
		_ = conn.SetReadDeadline(time.Now().Add(time.Second))
		_, copyErr := io.Copy(io.Discard, conn)
		closed <- copyErr
	}()
	return "ws://" + ln.Addr().String(), closed, func() { _ = ln.Close() }
}

// readServerFrame reads a (possibly masked) client frame.
func readServerFrame(r *bufio.Reader) (byte, []byte, error) {
	header := make([]byte, 2)
	if _, err := io.ReadFull(r, header); err != nil {
		return 0, nil, err
	}
	opcode := header[0] & 0x0F
	masked := header[1]&0x80 != 0
	lenByte := header[1] & 0x7F

	var length int
	switch lenByte {
	case 126:
		var ext [2]byte
		if _, err := io.ReadFull(r, ext[:]); err != nil {
			return 0, nil, err
		}
		length = int(binary.BigEndian.Uint16(ext[:]))
	case 127:
		var ext [8]byte
		if _, err := io.ReadFull(r, ext[:]); err != nil {
			return 0, nil, err
		}
		length = int(binary.BigEndian.Uint64(ext[:]))
	default:
		length = int(lenByte)
	}

	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(r, mask[:]); err != nil {
			return 0, nil, err
		}
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(r, payload); err != nil {
		return 0, nil, err
	}
	if masked {
		for i := range payload {
			payload[i] ^= mask[i%4]
		}
	}
	return opcode, payload, nil
}

// writeServerFrame writes an unmasked server frame.
func writeServerFrame(conn net.Conn, opcode byte, payload []byte) {
	writeServerFrameWithFIN(conn, true, opcode, payload)
}

func writeServerFrameWithFIN(conn net.Conn, fin bool, opcode byte, payload []byte) {
	first := byte(0x80 | opcode)
	if !fin {
		first = opcode
	}
	length := len(payload)
	var header []byte
	switch {
	case length <= 125:
		header = []byte{first, byte(length)}
	case length <= 65535:
		header = make([]byte, 4)
		header[0] = first
		header[1] = 126
		binary.BigEndian.PutUint16(header[2:], uint16(length))
	default:
		header = make([]byte, 10)
		header[0] = first
		header[1] = 127
		binary.BigEndian.PutUint64(header[2:], uint64(length))
	}
	_, _ = conn.Write(header)
	_, _ = conn.Write(payload)
}

func wsFragmentedTextServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("Sec-WebSocket-Key")
		accept := wsAccept(key)
		hj, ok := w.(http.Hijacker)
		if !ok {
			return
		}
		conn, bufrw, err := hj.Hijack()
		if err != nil {
			return
		}
		defer conn.Close()

		_, _ = bufrw.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n")
		_ = bufrw.Flush()

		writeServerFrameWithFIN(conn, false, 0x1, []byte("hel"))
		writeServerFrame(conn, 0x9, []byte("ping"))
		writeServerFrameWithFIN(conn, true, 0x0, []byte("lo"))

		reader := bufio.NewReader(conn)
		opcode, payload, err := readServerFrame(reader)
		if err != nil {
			return
		}
		if opcode != 0xA || string(payload) != "ping" {
			t.Errorf("client must answer ping with pong, got opcode=%d payload=%q", opcode, payload)
		}
	}))
}

func TestDialWebSocket_handshakeAndEcho(t *testing.T) {
	t.Parallel()
	srv := wsEchoServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	// Send text, expect echo.
	if err := ws.writeFrame(0x1, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	got, err := ws.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "hello" {
		t.Fatalf("got %q", got)
	}
}

func TestDialWebSocket_binaryFrame(t *testing.T) {
	t.Parallel()
	srv := wsEchoServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	payload := []byte{0x00, 0xFF, 0xAB, 0xCD}
	if err := ws.WriteBinary(payload); err != nil {
		t.Fatal(err)
	}
	got, err := ws.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("got %x", got)
	}
}

func TestDialWebSocket_pingPong(t *testing.T) {
	t.Parallel()
	srv := wsPingThenEchoServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	// Server sends a ping first; ReadMessage handles it internally by sending
	// a pong back and continuing to the next data frame.
	if err := ws.writeFrame(0x1, []byte("after-ping")); err != nil {
		t.Fatal(err)
	}
	got, err := ws.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "after-ping" {
		t.Fatalf("got %q", got)
	}
}

func TestDialWebSocket_reassemblesFragmentedTextWithInterleavedPing(t *testing.T) {
	t.Parallel()
	srv := wsFragmentedTextServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	got, err := ws.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "hello" {
		t.Fatalf("got %q", got)
	}
}

func TestDialWebSocket_closeFrame(t *testing.T) {
	t.Parallel()
	srv := wsEchoServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}

	// Server echoes close → ReadMessage returns io.EOF.
	_ = ws.Close()
	_, err = ws.ReadMessage()
	if err == nil {
		t.Fatal("expected error after close")
	}
}

func TestDialWebSocket_mediumFrame(t *testing.T) {
	t.Parallel()
	srv := wsEchoServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	// 200 bytes: uses the 16-bit extended length encoding (>125).
	payload := bytes.Repeat([]byte("A"), 200)
	if err := ws.WriteBinary(payload); err != nil {
		t.Fatal(err)
	}
	got, err := ws.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("got %d bytes, want 200", len(got))
	}
}

func TestDialWebSocket_largeFrame(t *testing.T) {
	t.Parallel()
	srv := wsEchoServer(t)
	defer srv.Close()

	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	// 70000 bytes: uses the 64-bit extended length encoding (>65535).
	payload := bytes.Repeat([]byte("B"), 70000)
	if err := ws.WriteBinary(payload); err != nil {
		t.Fatal(err)
	}
	got, err := ws.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("got %d bytes, want 70000", len(got))
	}
}

func TestDialWebSocket_unsupportedScheme(t *testing.T) {
	t.Parallel()
	_, err := dialWebSocket(context.Background(), "ftp://example.com", nil)
	if err == nil || !strings.Contains(err.Error(), "unsupported websocket scheme") {
		t.Fatalf("err=%v", err)
	}
}

func TestDialWebSocket_httpSchemeConverted(t *testing.T) {
	t.Parallel()
	srv := wsEchoServer(t)
	defer srv.Close()

	// Explicit http:// should work (gets converted to ws:// internally).
	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	ws.Close()
}

func TestExpectedWebSocketAccept(t *testing.T) {
	t.Parallel()
	// RFC 6455 section 4.2.2 example key.
	key := "dGhlIHNhbXBsZSBub25jZQ=="
	want := "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
	got := expectedWebSocketAccept(key)
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestWebsocketPayloadLength(t *testing.T) {
	t.Parallel()
	// <=125: direct
	got, err := websocketPayloadLength(strings.NewReader(""), 42)
	if err != nil || got != 42 {
		t.Fatalf("got %d err %v", got, err)
	}
	// 126: read 2 bytes
	ext16 := make([]byte, 2)
	binary.BigEndian.PutUint16(ext16, 300)
	got, err = websocketPayloadLength(bytes.NewReader(ext16), 126)
	if err != nil || got != 300 {
		t.Fatalf("got %d err %v", got, err)
	}
	// 127: read 8 bytes
	ext64 := make([]byte, 8)
	binary.BigEndian.PutUint64(ext64, 70000)
	got, err = websocketPayloadLength(bytes.NewReader(ext64), 127)
	if err != nil || got != 70000 {
		t.Fatalf("got %d err %v", got, err)
	}
}

func TestDialWebSocket_badHandshakeStatus(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		fmt.Fprint(w, "denied")
	}))
	defer srv.Close()
	_, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err == nil || !strings.Contains(err.Error(), "handshake failed") {
		t.Fatalf("err=%v", err)
	}
}

func TestDialWebSocket_closesConnectionOnHandshakeValidationFailure(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name     string
		response string
	}{
		{name: "non 101", response: "HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"},
		{name: "missing upgrade", response: "HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n"},
		{name: "missing connection", response: "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: x\r\n\r\n"},
		{name: "bad accept key", response: "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: bad\r\n\r\n"},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			rawURL, closed, cleanup := serveHandshakeFailure(t, tt.response)
			defer cleanup()
			_, err := dialWebSocket(context.Background(), rawURL, nil)
			if err == nil {
				t.Fatal("expected handshake failure")
			}
			select {
			case err := <-closed:
				require.NoError(t, err)
			case <-time.After(time.Second):
				t.Fatal("client did not close failed handshake connection")
			}
		})
	}
}

func TestDialWebSocketConn_wssHonorsContextDuringTLSHandshake(t *testing.T) {
	t.Parallel()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		conn, acceptErr := ln.Accept()
		if acceptErr == nil {
			defer conn.Close()
			<-time.After(2 * time.Second)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	parsed, err := url.Parse("wss://" + ln.Addr().String() + "/attach")
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	_, err = dialWebSocketConn(ctx, parsed)
	if err == nil {
		t.Fatal("expected TLS handshake failure")
	}
	if time.Since(started) > time.Second {
		t.Fatal("TLS websocket dial ignored context cancellation")
	}
}

func TestWebsocketDialAddress_defaultsIPv6HostWithoutPort(t *testing.T) {
	t.Parallel()
	parsed, err := url.Parse("wss://[::1]/test-tools")
	if err != nil {
		t.Fatal(err)
	}
	if got := websocketDialAddress(parsed); got != "[::1]:443" {
		t.Fatalf("address=%q", got)
	}
}

func TestReadFrameRejectsOversizedLength(t *testing.T) {
	// FIN+binary, unmasked, 64-bit length = 1 GiB — must be refused before allocation.
	hdr := []byte{0x82, 127, 0, 0, 0, 0, 0x40, 0, 0, 0}
	c := &WebSocketConn{r: bufio.NewReader(bytes.NewReader(hdr))}
	_, _, _, err := c.readFrame()
	if err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("oversized frame accepted: %v", err)
	}
}

// RFC 6455 section 5.4: a fragment may be empty. An empty first fragment followed
// by a final continuation must yield the continuation's payload, and a new
// data frame arriving mid-fragment is a protocol error even when the
// in-progress message has no bytes yet.
func TestDialWebSocket_emptyFirstFragmentAndMidFragmentDataFrame(t *testing.T) {
	t.Parallel()
	serve := func(frames func(net.Conn)) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			accept := wsAccept(r.Header.Get("Sec-WebSocket-Key"))
			hj, ok := w.(http.Hijacker)
			if !ok {
				return
			}
			conn, bufrw, err := hj.Hijack()
			if err != nil {
				return
			}
			defer conn.Close()
			_, _ = bufrw.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n")
			_ = bufrw.Flush()
			frames(conn)
			time.Sleep(100 * time.Millisecond)
		}))
	}

	srv := serve(func(conn net.Conn) {
		writeServerFrameWithFIN(conn, false, 0x1, nil) // empty first fragment
		writeServerFrameWithFIN(conn, true, 0x0, []byte("hello"))
	})
	defer srv.Close()
	ws, err := dialWebSocket(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	got, err := ws.ReadMessage()
	_ = ws.Close()
	if err != nil || string(got) != "hello" {
		t.Fatalf("empty first fragment: got %q err %v", got, err)
	}

	srv2 := serve(func(conn net.Conn) {
		writeServerFrameWithFIN(conn, false, 0x1, nil)          // empty, unfinished
		writeServerFrameWithFIN(conn, true, 0x1, []byte("new")) // a NEW data frame mid-fragment
	})
	defer srv2.Close()
	ws2, err := dialWebSocket(context.Background(), srv2.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	_, err = ws2.ReadMessage()
	_ = ws2.Close()
	if err == nil || !strings.Contains(err.Error(), "before finishing a fragmented message") {
		t.Fatalf("a data frame mid-fragment must be a protocol error, got %v", err)
	}
}

func TestAttachWebSocket_sendsIdentityHeaders(t *testing.T) {
	t.Parallel()
	var mu sync.Mutex
	seen := http.Header{}
	var query url.Values
	echo := wsEchoServer(t)
	defer echo.Close()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = r.Header.Clone()
		query = r.URL.Query()
		mu.Unlock()
		echo.Config.Handler.ServeHTTP(w, r)
	}))
	defer srv.Close()

	c := New(api.New(srv.URL, "tok", "test"))
	ws, err := c.AttachWebSocket(context.Background(), "p1", "")
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()

	mu.Lock()
	defer mu.Unlock()
	if seen.Get("X-Daytona-Source") != "cli" || seen.Get("X-Daytona-SDK-Version") != "test" || seen.Get("User-Agent") != "cli/test" {
		t.Fatalf("identity headers on handshake: %v", seen)
	}
	// The browser-style query credential is daytona_token, and it is the only
	// credential parameter sent.
	if query.Get("daytona_token") != "tok" {
		t.Fatalf("query credential on handshake: %v", query)
	}
	for key := range query {
		if key != "daytona_token" && strings.HasSuffix(key, "_token") {
			t.Fatalf("unexpected credential parameter %q on handshake: %v", key, query)
		}
	}
}
