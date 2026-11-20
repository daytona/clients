// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/base64"
	"fmt"
	"io"
	"iter"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func buildLogsFake(t *testing.T, handler http.HandlerFunc) *fakeServer {
	t.Helper()
	return newFakeServer(t, map[string]http.HandlerFunc{
		"GET /{collection}/{id}/build-logs-url": func(w http.ResponseWriter, r *http.Request) {
			writeJSON(t, w, map[string]string{
				"url": "http://" + r.Host + "/prefix/build-logs/" + r.PathValue("collection") + "/" + r.PathValue("id"),
			})
		},
		"GET /prefix/build-logs/{collection}/{id}": handler,
	})
}

func buildLogsSSE(t *testing.T, body string) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "Text/Event-Stream; charset=utf-8")
		if _, err := io.WriteString(w, body); err != nil {
			t.Errorf("write SSE: %v", err)
		}
	}
}

func buildLogsCollect(sequence iter.Seq2[BuildLogEvent, error]) ([]BuildLogEvent, error) {
	var events []BuildLogEvent
	for event, err := range sequence {
		if err != nil {
			return events, err
		}
		events = append(events, event)
	}
	return events, nil
}

func buildLogsAs[T BuildLogEvent](t *testing.T, event BuildLogEvent) T {
	t.Helper()
	value, ok := event.(T)
	require.True(t, ok, "unexpected event %T", event)
	return value
}

func buildLogsChunk(data string, start uint64) string {
	return fmt.Sprintf("event: log\nid: checkpoint/%d+=\ndata: "+
		`{"data_base64":%q,"start_offset":"%d","next_offset":"%d"}`+"\n\n",
		start+uint64(len(data)), base64.StdEncoding.EncodeToString([]byte(data)), start, start+uint64(len(data)))
}

func buildLogsDone(cursor string) string {
	return "event: done\ndata: " + fmt.Sprintf(
		`{"status":"ready","reason":"terminal","output_complete":true,"cursor":%q}`, cursor) + "\n\n"
}

func TestBuildLogsFollowDeliversTypedEvents(t *testing.T) {
	// Given server execution metadata already in the past and unknown events.
	body := "\uFEFFevent: state\r\ndata: {\"status\":\"building\",\r\ndata: \"deadline_at\":\"2000-01-01T00:00:00Z\"}\r\n\r\n" +
		"event: future_kind\nid: ignored\ndata: not JSON\n\n" +
		buildLogsChunk("\xf0\x9f", 0) + buildLogsChunk("\x98\x80!", 2) +
		buildLogsDone("checkpoint/5+=")
	fake := buildLogsFake(t, buildLogsSSE(t, body))
	client := newTestClient(t, fake.Server)
	// When following the public image API.
	events, err := buildLogsCollect((&Images{client: client}).FollowBuildLogs(t.Context(), "image", nil))
	// Then only known events are emitted, without altering the raw bytes.
	require.NoError(t, err)
	require.Len(t, events, 4)
	state := buildLogsAs[BuildLogStateEvent](t, events[0])
	require.Equal(t, 2000, state.DeadlineAt.Year())
	require.Equal(t, "building", state.Status)
	require.Equal(t, []byte("\xf0\x9f"), buildLogsAs[BuildLogChunkEvent](t, events[1]).Data)
	require.Equal(t, []byte("\x98\x80!"), buildLogsAs[BuildLogChunkEvent](t, events[2]).Data)
	require.Equal(t, BuildLogFollowResult{
		Status: "ready", Reason: BuildLogDoneReasonTerminal, RawReason: "terminal",
		Cursor: "checkpoint/5+=", OutputComplete: true,
	}, buildLogsAs[BuildLogDoneEvent](t, events[3]).Result)
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.Equal(t, "/images/image/build-logs-url", requests[0].Path)
	require.Equal(t, "true", requests[1].Query.Get("follow"))
	require.Equal(t, "sse", requests[1].Query.Get("format"))
	require.Equal(t, "text/event-stream", requests[1].Header.Get("Accept"))
	require.Equal(t, requests[0].Header.Get("Authorization"), requests[1].Header.Get("Authorization"))
}

func TestBuildLogsGapMakesCompletionIncomplete(t *testing.T) {
	// Given a resumed stream with a future gap reason.
	body := "event: gap\nid: gap-cursor\ndata: " +
		`{"reason":"expired","requested_offset":"40","first_available_offset":"44","dropped_bytes":"4"}` + "\n\n" +
		buildLogsChunk("x", 44) +
		"event: done\ndata: " +
		`{"status":"failed","reason":"future","output_complete":true,"cursor":"checkpoint/45+="}` + "\n\n"
	fake := buildLogsFake(t, buildLogsSSE(t, body))
	client := newTestClient(t, fake.Server)
	// When following a sandbox without cached metadata.
	events, err := buildLogsCollect((&Sandbox{client: client, id: "sbx"}).FollowBuildLogs(
		t.Context(), &FollowBuildLogsOptions{Cursor: "resume opaque: +/="}))
	// Then the unknown enum and gap are visible and failed status is completion.
	require.NoError(t, err)
	require.Len(t, events, 3)
	gap := buildLogsAs[BuildLogGapEvent](t, events[0])
	require.Equal(t, BuildLogGapReasonUnknown, gap.Reason)
	require.Equal(t, "expired", gap.RawReason)
	require.Equal(t, uint64(4), gap.DroppedBytes)
	result := buildLogsAs[BuildLogDoneEvent](t, events[2]).Result
	require.Equal(t, "failed", result.Status)
	require.Equal(t, BuildLogDoneReasonUnknown, result.Reason)
	require.Equal(t, "future", result.RawReason)
	require.False(t, result.OutputComplete)
	require.Equal(t, "resume opaque: +/=", fake.requests()[1].Query.Get("cursor"))
}

func TestBuildLogsOptionsAreSnapshotted(t *testing.T) {
	// Given an iterator created before options are modified.
	fake := buildLogsFake(t, buildLogsSSE(t, buildLogsDone("original")))
	client := newTestClient(t, fake.Server)
	opts := &FollowBuildLogsOptions{Cursor: "original"}
	sequence := (&Images{client: client}).FollowBuildLogs(t.Context(), "image", opts)
	opts.Cursor = "changed"
	// When iteration starts.
	_, err := buildLogsCollect(sequence)
	// Then the original cursor is used.
	require.NoError(t, err)
	require.Equal(t, "original", fake.requests()[1].Query.Get("cursor"))
}
