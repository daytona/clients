// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bytes"
	"encoding/base64"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
)

func TestWriteLogFrames_routesStdoutStderr(t *testing.T) {
	t.Parallel()
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	frames := []tbgen.LogFrameDto{
		{Channel: "stdout", Data: "out1"},
		{Channel: "stderr", Data: "err1"},
		{Channel: "stdout", Data: "out2"},
		{Channel: "error", Data: "err2"},
	}
	if err := WriteLogFrames(frames, "text", stdout, stderr); err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "out1out2" {
		t.Fatalf("stdout=%q", stdout.String())
	}
	if stderr.String() != "err1err2" {
		t.Fatalf("stderr=%q", stderr.String())
	}
}

func TestWriteLogFrames_nilWriterSkipped(t *testing.T) {
	t.Parallel()
	frames := []tbgen.LogFrameDto{
		{Channel: "stdout", Data: "data"},
	}
	// nil stdout — should not panic.
	if err := WriteLogFrames(frames, "text", nil, nil); err != nil {
		t.Fatal(err)
	}
}

func TestWriteLogFrames_base64Decoding(t *testing.T) {
	t.Parallel()
	stdout := &bytes.Buffer{}
	encoded := base64.StdEncoding.EncodeToString([]byte("decoded"))
	frames := []tbgen.LogFrameDto{
		{Channel: "stdout", Data: encoded},
	}
	if err := WriteLogFrames(frames, "base64", stdout, nil); err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "decoded" {
		t.Fatalf("got %q", stdout.String())
	}
}

func TestDecodeFrameData_text(t *testing.T) {
	t.Parallel()
	got, err := decodeFrameData("hello", "text")
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "hello" {
		t.Fatalf("got %q", got)
	}
}

func TestDecodeFrameData_base64(t *testing.T) {
	t.Parallel()
	encoded := base64.StdEncoding.EncodeToString([]byte("binary\x00data"))
	got, err := decodeFrameData(encoded, "base64")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, []byte("binary\x00data")) {
		t.Fatalf("got %x", got)
	}
}

func TestDecodeFrameData_base64Invalid(t *testing.T) {
	t.Parallel()
	_, err := decodeFrameData("not!valid!base64!", "base64")
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestResultFromProcess(t *testing.T) {
	t.Parallel()
	code := int32(42)
	reason := tbgen.ReasonExited
	signal := "SIGTERM"
	p := tbgen.ProcessDto{
		ExitCode: &code,
		Reason:   &reason,
		Signal:   &signal,
	}
	r := resultFromProcess(p)
	if r.ExitCode == nil || *r.ExitCode != 42 {
		t.Fatalf("exitCode=%v", r.ExitCode)
	}
	if r.Reason == nil || *r.Reason != tbgen.ReasonExited {
		t.Fatalf("reason=%v", r.Reason)
	}
	// normalizeExit strips SIG prefix.
	if r.Signal == nil || *r.Signal != "TERM" {
		t.Fatalf("signal=%v", r.Signal)
	}
}

func TestNormalizeExit_nilSignal(t *testing.T) {
	t.Parallel()
	r := normalizeExit(tbgen.ExitResult{})
	if r.Signal != nil {
		t.Fatalf("signal=%v", r.Signal)
	}
}

func TestNormalizeExit_stripsPrefix(t *testing.T) {
	t.Parallel()
	sig := "SIGINT"
	r := normalizeExit(tbgen.ExitResult{Signal: &sig})
	if r.Signal == nil || *r.Signal != "INT" {
		t.Fatalf("signal=%v", r.Signal)
	}
}

func TestStreamProcess_closesBlockingStdinOnceProcessFinishes(t *testing.T) {
	t.Parallel()
	reason := tbgen.ReasonExited
	code := int32(0)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/processes":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"proc","state":"running","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}}`))
		case "/processes/proc/logs":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte("event: state\ndata: {\"reason\":\"exited\",\"exitCode\":0}\n\nevent: eof\ndata: {}\n\n"))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	client := newToolboxClient(srv)
	stdinReader, stdinWriter := io.Pipe()
	defer stdinWriter.Close()
	result, err := client.StreamProcess(t.Context(), StreamProcessOptions{
		Request: tbgen.CreateProcessRequest{Argv: &[]string{"true"}},
		Stdout:  io.Discard,
		Stderr:  io.Discard,
		Stdin:   stdinReader,
	})
	require.NoError(t, err)
	require.Equal(t, &reason, result.Exit.Reason)
	require.Equal(t, &code, result.Exit.ExitCode)
	writeDone := make(chan error, 1)
	go func() {
		_, writeErr := stdinWriter.Write([]byte("still open?"))
		writeDone <- writeErr
	}()
	require.ErrorIs(t, <-writeDone, io.ErrClosedPipe)
}

// Process `base64` frames are base64url without padding: bytes that
// encode to `-`/`_` (0xfb 0xff …) must round-trip, as must padded standard
// base64 from any other producer.
func TestDecodeFrameData_acceptsBase64URLWithoutPadding(t *testing.T) {
	payload := []byte{0xfb, 0xff, 0xbf, 0x00, 0x01, 0x02, 'x'}
	urlRaw := base64.RawURLEncoding.EncodeToString(payload) // contains - and _
	if !strings.ContainsAny(urlRaw, "-_") {
		t.Fatalf("vector must exercise the url alphabet: %q", urlRaw)
	}
	for _, raw := range []string{urlRaw, base64.URLEncoding.EncodeToString(payload), base64.StdEncoding.EncodeToString(payload), base64.RawStdEncoding.EncodeToString(payload)} {
		got, err := decodeFrameData(raw, "base64")
		if err != nil {
			t.Fatalf("%q: %v", raw, err)
		}
		if string(got) != string(payload) {
			t.Fatalf("%q: got %x want %x", raw, got, payload)
		}
	}
	if _, err := decodeFrameData("not base64!", "base64"); err == nil {
		t.Fatal("garbage must fail")
	}
	if got, _ := decodeFrameData("plain", "text"); string(got) != "plain" {
		t.Fatal(string(got))
	}
}
