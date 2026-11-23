// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGitParsing_whenRequiredFieldIsMissingOrWrong(t *testing.T) {
	for _, test := range []struct {
		name, path string
		fixture    string
		fields     []string
		call       func(context.Context, *Git) error
	}{
		{
			"status", "status", `{"currentBranch":"main","ahead":0,"behind":0,"branchPublished":true,"fileStatus":[]}`,
			[]string{"currentBranch", "ahead", "behind", "branchPublished", "fileStatus"},
			func(ctx context.Context, g *Git) error { _, e := g.Status(ctx, "repo"); return e },
		},
		{
			"remote", "remotes", `{"name":"origin","url":"https://example.org/repo"}`,
			[]string{"name", "url"},
			func(ctx context.Context, g *Git) error { _, e := g.ListRemotes(ctx, "repo"); return e },
		},
		{
			"file", "status", `{"name":"file","staging":"M","worktree":" ","extra":""}`,
			[]string{"name", "staging", "worktree", "extra"},
			func(ctx context.Context, g *Git) error { _, e := g.Status(ctx, "repo"); return e },
		},
		{
			"history", "history", `{"hash":"h","message":"m","author":"a","email":"e","timestamp":"2026-01-01T00:00:00Z"}`,
			[]string{"hash", "message", "author", "email", "timestamp"},
			func(ctx context.Context, g *Git) error { _, e := g.History(ctx, "repo"); return e },
		},
	} {
		for _, name := range test.fields {
			for _, malformed := range []string{"missing", "null", "object"} {
				t.Run(test.name+"/"+name+"/"+malformed, func(t *testing.T) {
					// Given
					object := map[string]json.RawMessage{}
					require.NoError(t, json.Unmarshal([]byte(test.fixture), &object))
					switch malformed {
					case "missing":
						delete(object, name)
					case "null":
						object[name] = json.RawMessage("null")
					case "object":
						object[name] = json.RawMessage("{}")
					}
					payload, err := json.Marshal(object)
					require.NoError(t, err)
					switch test.name {
					case "remote":
						payload = []byte(`{"remotes":[` + string(payload) + `]}`)
					case "file":
						payload = []byte(`{"currentBranch":"main","ahead":0,"behind":0,"branchPublished":true,"fileStatus":[` + string(payload) + `]}`)
					case "history":
						payload = []byte(`[` + string(payload) + `]`)
					}
					git, _ := gitTestService(t, map[string]http.HandlerFunc{
						"/toolbox/sbx/git/" + test.path: jsonHandler(t, json.RawMessage(payload)),
					})
					// When
					err = test.call(t.Context(), git)
					// Then
					require.ErrorIs(t, err, ErrBadRequest)
					var sdk *Error
					require.ErrorAs(t, err, &sdk)
					require.Contains(t, sdk.Message, name)
				})
			}
		}
	}
}
