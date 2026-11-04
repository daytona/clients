// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestReservationList_forwardsPagingAndReturnsContinuation(t *testing.T) {
	// Given a reservation page with a continuation.
	item := macReservationFixture(`"m4-10c-16g"`, m4Capacity)
	requests := []expectedRequest{{
		method: http.MethodGet, path: "/reservations", query: "cursor=page-1&limit=2", status: 200,
		response: `{"reservations":[` + item + `],"next_cursor":"page-2"}`,
	}}

	// When requesting one page.
	stdout, _, err := runCLI(t, []string{"reservation", "list", "--limit", "2", "--cursor", "page-1"}, requests)

	// Then preserve the continuation without fetching another page.
	require.NoError(t, err)
	payload := requireJSONStdout(t, stdout)
	require.Equal(t, "page-2", payload["next_cursor"])
	require.Len(t, payload["items"], 1)
}

func TestReservationList_followsEveryPageWhenAll(t *testing.T) {
	// Given two distinct reservations on successive pages.
	first := macReservationFixture(`"m4-10c-16g"`, m4Capacity)
	second := strings.ReplaceAll(first, "res-1", "res-2")
	requests := []expectedRequest{
		{method: http.MethodGet, path: "/reservations", query: "limit=1", status: 200,
			response: `{"reservations":[` + first + `],"next_cursor":"page-2"}`},
		{method: http.MethodGet, path: "/reservations", query: "cursor=page-2&limit=1", status: 200,
			response: `{"reservations":[` + second + `],"next_cursor":null}`},
	}

	// When explicitly requesting all pages.
	stdout, _, err := runCLI(t, []string{"reservation", "list", "--all", "--limit", "1"}, requests)

	// Then both reservations appear in order, with no continuation.
	require.NoError(t, err)
	require.JSONEq(t, `{"items":[`+first+`,`+second+`],"incomplete":false}`, stdout)
}

func TestReservationList_refusesRepeatedCursorWhenAll(t *testing.T) {
	// Given a page whose continuation repeats its input cursor.
	requests := []expectedRequest{{
		method: http.MethodGet, path: "/reservations", query: "cursor=page-2&limit=100", status: 200,
		response: `{"reservations":[],"next_cursor":"page-2"}`,
	}}

	// When following every page.
	_, _, err := runCLI(t, []string{"reservation", "list", "--all", "--cursor", "page-2"}, requests)

	// Then stop instead of looping indefinitely.
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
}
