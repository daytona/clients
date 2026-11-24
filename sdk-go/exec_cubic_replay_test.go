// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExec_whenInlineLogsIncomplete_continuesFromInlineCursor(t *testing.T) {
	for _, state := range []string{"running", "terminal"} {
		t.Run(state, func(t *testing.T) {
			// Given initial output survives only in the create response.
			initial := processLogsFixture(processFrameFixture("stdout", "initial"))
			initial["eof"], initial["nextCursor"] = false, "after-initial"
			fixture := processFixture("p")
			fixture["state"], fixture["reason"], fixture["logs"] = state, "exited", initial
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes":       jsonHandler(t, fixture),
				"GET /processes/p/wait": jsonHandler(t, map[string]string{"reason": "exited"}),
				"GET /processes/p/logs": func(w http.ResponseWriter, r *http.Request) {
					page := processLogsFixture(processFrameFixture("stderr", "tail"))
					if r.URL.Query().Get("cursor") == "" {
						page["truncatedHead"] = true
					}
					writeJSON(t, w, page)
				},
			})
			// When collecting output after completion.
			result, err := sandbox.Exec(t.Context(), "command", nil)
			// Then retained-head eviction cannot discard the inline frames.
			require.NoError(t, err)
			require.Equal(t, "initialtail", result.Output)
			require.Len(t, result.Logs, 2)
			requests := fake.requests()
			require.Equal(t, "after-initial", requests[len(requests)-1].Query.Get("cursor"))
		})
	}
}

func TestExec_whenInlineLogsContinue_boundsReplay(t *testing.T) {
	for _, entry := range []struct {
		name      string
		next      string
		wantPages int
	}{
		{name: "live output", wantPages: 7},
		{name: "cursor repeats", next: "initial-end", wantPages: 1},
	} {
		t.Run(entry.name, func(t *testing.T) {
			// Given a timed-out process with inline output and continuing replay.
			initial := processLogsFixture(processFrameFixture("stdout", "i"))
			initial["eof"], initial["nextCursor"] = false, "initial-end"
			fixture := processFixture("p")
			fixture["state"], fixture["reason"], fixture["logs"] = "terminal", "timed_out", initial
			pages := 0
			sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes": jsonHandler(t, fixture),
				"GET /processes/p/logs": func(w http.ResponseWriter, _ *http.Request) {
					pages++
					page := processLogsFixture(processFrameFixture("stdout", "r"))
					page["eof"], page["nextCursor"] = false, entry.next
					if entry.next == "" {
						page["nextCursor"] = strconv.Itoa(pages)
					}
					writeJSON(t, w, page)
				},
			})
			// When collecting the bounded snapshot, including its inline page.
			result, err := sandbox.Exec(t.Context(), "command", nil)
			// Then both the page cap and cursor-cycle guard apply across that page.
			require.NoError(t, err)
			require.Equal(t, entry.wantPages, pages)
			require.Len(t, result.Logs, entry.wantPages+1)
			require.Equal(t, "i", result.Logs[0].Data)
		})
	}
}

func TestExec_whenInlineLogsEmpty_collectsOutputAfterWait(t *testing.T) {
	for _, cursor := range []string{"", "before-output"} {
		t.Run("cursor="+cursor, func(t *testing.T) {
			// Given creation precedes output, and the process finishes during wait.
			initial := processLogsFixture()
			initial["eof"], initial["nextCursor"] = false, cursor
			fixture := processFixture("p")
			fixture["logs"] = initial
			waited := false
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes": jsonHandler(t, fixture),
				"GET /processes/p/wait": func(w http.ResponseWriter, _ *http.Request) {
					waited = true
					writeJSON(t, w, map[string]string{"reason": "exited"})
				},
				"GET /processes/p/logs": func(w http.ResponseWriter, _ *http.Request) {
					if !waited {
						t.Error("replay started before waiting")
					}
					writeJSON(t, w, processLogsFixture(processFrameFixture("stdout", "after-wait")))
				},
			})
			// When executing through the blocking public surface.
			result, err := sandbox.Exec(t.Context(), "command", nil)
			// Then an empty historical snapshot cannot hide later output.
			require.NoError(t, err)
			require.Equal(t, "after-wait", result.Output)
			requests := fake.requests()
			require.Len(t, requests, 3)
			require.Equal(t, cursor, requests[2].Query.Get("cursor"))
		})
	}
}

func TestExec_whenFetchedLogsEmpty_stopsReplay(t *testing.T) {
	// Given replay after an empty create snapshot also finds no output.
	initial := processLogsFixture()
	initial["eof"], initial["nextCursor"] = false, "initial-end"
	fixture := processFixture("p")
	fixture["logs"] = initial
	pages := 0
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes":       jsonHandler(t, fixture),
		"GET /processes/p/wait": jsonHandler(t, map[string]string{"reason": "exited"}),
		"GET /processes/p/logs": func(w http.ResponseWriter, _ *http.Request) {
			pages++
			page := processLogsFixture()
			page["eof"], page["nextCursor"] = false, strconv.Itoa(pages)
			if pages > 1 {
				page = processLogsFixture(processFrameFixture("stdout", "unexpected"))
			}
			writeJSON(t, w, page)
		},
	})
	// When collecting after wait.
	result, err := sandbox.Exec(t.Context(), "command", nil)
	// Then an empty fetched page stops replay, despite its advancing cursor.
	require.NoError(t, err)
	require.Empty(t, result.Output)
	require.Equal(t, 1, pages)
}
