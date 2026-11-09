// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package output

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSnakeCaseKey(t *testing.T) {
	for in, want := range map[string]string{
		"exitCode":             "exit_code",
		"nextCursor":           "next_cursor",
		"truncatedHead":        "truncated_head",
		"firstAvailableCursor": "first_available_cursor",
		"processId":            "process_id",
		"processID":            "process_id",
		"timeoutMs":            "timeout_ms",
		"id":                   "id",
		"already_snake":        "already_snake",
		"HTTPStatus":           "http_status",
		"userIDs":              "user_ids",
		"IPv4":                 "ipv4",
		"URLs":                 "urls",
		"ABTest":               "ab_test",
	} {
		if got := SnakeCaseKey(in); got != want {
			t.Errorf("SnakeCaseKey(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestSnakeCase_convertsNestedKeysNotValues(t *testing.T) {
	type frame struct {
		ExitCode int    `json:"exitCode"`
		Data     string `json:"data"`
	}
	v, err := SnakeCase(map[string]any{"nextCursor": "abcDef", "frames": []frame{{ExitCode: 3, Data: "camelCase"}}})
	if err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(v)
	if got, want := string(b), `{"frames":[{"data":"camelCase","exit_code":3}],"next_cursor":"abcDef"}`; got != want {
		t.Fatalf("got %s want %s", got, want)
	}
}

func TestNDJSONDataWriter_keepsCharactersWhole(t *testing.T) {
	var out strings.Builder
	w := NewNDJSONDataWriter(&out)
	euro := []byte("€") // three bytes
	_, _ = w.Write(append([]byte("a"), euro[:1]...))
	_, _ = w.Write(euro[1:])
	_, _ = w.Write([]byte("<b>\n"))
	if err := w.Flush(); err != nil {
		t.Fatal(err)
	}
	want := "{\"data\":\"a\"}\n{\"data\":\"€\"}\n{\"data\":\"<b>\\n\"}\n"
	if out.String() != want {
		t.Fatalf("got %q want %q", out.String(), want)
	}
}
