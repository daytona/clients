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

func TestInterpreterContexts_whenResponseIDInvalid_rejectsBeforeBinding(t *testing.T) {
	cases := []struct {
		name, route, prefix, suffix string
		call                        func(context.Context, *Interpreter) (bool, error)
	}{
		{"create", "POST /sessions", "", "", func(ctx context.Context, service *Interpreter) (bool, error) {
			result, err := service.CreateContext(ctx, "python", nil)
			return result == nil, err
		}},
		{"named create", "POST /sessions", "", "", func(ctx context.Context, service *Interpreter) (bool, error) {
			result, err := service.CreateContext(ctx, "python", &CreateInterpreterContextOptions{ContextID: "requested"})
			return result == nil, err
		}},
		{"list", "GET /sessions", `{"items":[`, "]}", func(ctx context.Context, service *Interpreter) (bool, error) {
			result, err := service.ListContexts(ctx)
			return result == nil, err
		}},
	}
	for _, tc := range cases {
		for _, id := range []string{`"\ud800"`, `"\udfff"`, `"\ufffd"`, `" "`, `"."`, `".."`} {
			t.Run(tc.name+"/"+id, func(t *testing.T) {
				// Given a server response with an unusable context ID.
				payload := json.RawMessage(tc.prefix + `{"id":` + id + `,"kernel":"python"}` + tc.suffix)
				fake := newFakeServer(t, map[string]http.HandlerFunc{tc.route: jsonHandler(t, payload)})
				service := interpreterTestSandbox(t, fake).Interpreter()
				// When the context response is parsed.
				empty, err := tc.call(t.Context(), service)
				// Then no unusable context is exposed or followed.
				require.ErrorIs(t, err, ErrBadRequest)
				require.True(t, empty)
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}
