// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"fmt"
	"io"
	"strings"
	"testing"
)

func TestReadSSE_dataEventID(t *testing.T) {
	t.Parallel()
	input := "event: status\ndata: hello\nid: 42\n\n"
	var got []sseMessage
	if err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d messages, want 1", len(got))
	}
	if got[0].Event != "status" || got[0].Data != "hello" || got[0].ID != "42" {
		t.Fatalf("got %+v", got[0])
	}
}

func TestReadSSE_CRLFandLF(t *testing.T) {
	t.Parallel()
	input := "data: alpha\r\n\r\ndata: beta\n\n"
	var got []sseMessage
	if err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d messages, want 2", len(got))
	}
	if got[0].Data != "alpha" || got[1].Data != "beta" {
		t.Fatalf("got %q %q", got[0].Data, got[1].Data)
	}
}

func TestReadSSE_multiLineData(t *testing.T) {
	t.Parallel()
	input := "data: line1\ndata: line2\ndata: line3\n\n"
	var got []sseMessage
	if err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d messages", len(got))
	}
	if got[0].Data != "line1\nline2\nline3" {
		t.Fatalf("data=%q", got[0].Data)
	}
}

func TestReadSSE_commentsIgnored(t *testing.T) {
	t.Parallel()
	input := ": this is a comment\ndata: visible\n\n"
	var got []sseMessage
	if err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Data != "visible" {
		t.Fatalf("got %+v", got)
	}
}

func TestReadSSE_emptyEventSkipped(t *testing.T) {
	t.Parallel()
	// An event block with no data/id/event fields (only comments) yields
	// an empty sseMessage which readSSE skips.
	input := ": just a comment\n\ndata: real\n\n"
	var got []sseMessage
	if err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Data != "real" {
		t.Fatalf("got %+v", got)
	}
}

func TestReadSSE_midEventEOF(t *testing.T) {
	t.Parallel()
	// Stream ends without a trailing blank line — the partial block is still dispatched.
	input := "data: partial"
	var got []sseMessage
	if err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Data != "partial" {
		t.Fatalf("got %+v", got)
	}
}

func TestReadSSE_defaultEventIsMessage(t *testing.T) {
	t.Parallel()
	input := "data: hello\n\n"
	var got []sseMessage
	_ = readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	})
	if len(got) != 1 || got[0].Event != "message" {
		t.Fatalf("event=%q", got[0].Event)
	}
}

func TestReadSSE_skipsEmptyDataBlocksButKeepsLastEventID(t *testing.T) {
	t.Parallel()
	input := "id: c123\n\nevent: log\n\ndata: payload\n\n"
	var got []sseMessage
	err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d messages, want 1", len(got))
	}
	if got[0].Data != "payload" || got[0].ID != "c123" || got[0].Event != "message" {
		t.Fatalf("got %+v", got[0])
	}
	if msg := parseSSEMessage("event: log\n"); msg != (sseMessage{Event: "log"}) {
		t.Fatalf("parse should keep the empty-data block non-dispatchable: %+v", msg)
	}
}

func TestReadSSE_callbackErrorStopsRead(t *testing.T) {
	t.Parallel()
	input := "data: a\n\ndata: b\n\n"
	count := 0
	err := readSSE(strings.NewReader(input), func(m sseMessage) error {
		count++
		return fmt.Errorf("stop")
	})
	if err == nil || err.Error() != "stop" {
		t.Fatalf("err=%v", err)
	}
	if count != 1 {
		t.Fatalf("count=%d", count)
	}
}

func TestReadSSE_bareCR(t *testing.T) {
	t.Parallel()
	// Bare CR (no LF) is also a line terminator per the SSE spec.
	input := "data: cr\r\rdata: next\r\r"
	var got []sseMessage
	_ = readSSE(strings.NewReader(input), func(m sseMessage) error {
		got = append(got, m)
		return nil
	})
	if len(got) != 2 || got[0].Data != "cr" || got[1].Data != "next" {
		t.Fatalf("got %+v", got)
	}
}

func TestParseSSEMessage_fieldWithoutColon(t *testing.T) {
	t.Parallel()
	// A field line with no colon: the whole line is the field name, value is "".
	msg := parseSSEMessage("event\n")
	// "event" with empty string value becomes the event field → "".
	// Since data="" and id="" and event="", this would be the empty message.
	// The field name "event" with value "" sets Event="".
	if msg.Event != "" {
		t.Fatalf("event=%q", msg.Event)
	}
}

func TestParseSSEMessage_dataWithLeadingSpace(t *testing.T) {
	t.Parallel()
	msg := parseSSEMessage("data:  two spaces\n")
	// Only the first space after the colon is stripped.
	if msg.Data != " two spaces" {
		t.Fatalf("data=%q", msg.Data)
	}
}

func TestReadSSERejectsEndlessLine(t *testing.T) {
	r := io.LimitReader(zeroReader{}, maxSSELine+1024)
	err := readSSE(r, func(sseMessage) error { return nil })
	if err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("endless line accepted: %v", err)
	}
}

// An unterminated final line is held to the event cap like any other line.
func TestReadSSERejectsOversizedEventEndingAtEOF(t *testing.T) {
	line := "data: " + strings.Repeat("a", 1<<20-7) + "\n" // 1 MiB per line
	full := strings.Repeat(line, maxSSEEvent/len(line))
	tail := "data: " + strings.Repeat("b", maxSSELine-16) // no newline: ends at EOF
	called := false
	err := readSSE(strings.NewReader(full+tail), func(sseMessage) error { called = true; return nil })
	if err == nil || !strings.Contains(err.Error(), "SSE event exceeds") || called {
		t.Fatalf("oversized final event accepted: err=%v called=%v", err, called)
	}
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = 'a'
	}
	return len(p), nil
}
