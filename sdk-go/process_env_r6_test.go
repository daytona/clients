// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestProcessCreatePreservesEnv_whenEmptyOrAbsent(t *testing.T) {
	for _, operation := range []string{"start", "start args", "exec", "exec args", "stream exec", "session", "code", "stream code"} {
		for _, tc := range []struct {
			name string
			env  map[string]string
		}{
			{"absent", nil}, {"empty", map[string]string{}}, {"populated", map[string]string{"A": "B"}},
		} {
			t.Run(operation+"/"+tc.name, func(t *testing.T) {
				// Given a process creator with an optional environment.
				env := tc.env
				info := processFixture("p")
				info["state"], info["reason"], info["exitCode"] = "terminal", "exited", 0
				sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
					"POST /processes":       jsonHandler(t, info),
					"GET /processes/p/wait": jsonHandler(t, map[string]any{"reason": "exited", "exitCode": 0}),
					"GET /processes/p/logs": func(w http.ResponseWriter, r *http.Request) {
						if r.URL.Query().Get("follow") == "true" {
							interpreterSSE(t, w, "event: eof\nid: 0\ndata: {}\n\n")
							return
						}
						writeJSON(t, w, interpreterEmptyLogs())
					},
				})
				start := &ProcessStartOptions{Env: env}
				exec := &ExecOptions{Env: env}
				code := &RunCodeOptions{Env: env}
				// When creating through each public entry point.
				var err error
				switch operation {
				case "start":
					_, err = sandbox.Process().Start(t.Context(), "true", start)
				case "start args":
					_, err = sandbox.Process().StartArgs(t.Context(), []string{"true"}, start)
				case "exec":
					_, err = sandbox.Exec(t.Context(), "true", exec)
				case "exec args":
					_, err = sandbox.ExecArgs(t.Context(), []string{"true"}, exec)
				case "stream exec":
					exec.Stdout = io.Discard
					_, err = sandbox.Exec(t.Context(), "true", exec)
				case "session":
					_, err = sandbox.Process().CreateSession(t.Context(), &CreateSessionOptions{Env: env})
				case "code", "stream code":
					if operation == "stream code" {
						code.Stdout = io.Discard
					}
					_, err = sandbox.RunCode(t.Context(), "1", code)
				}
				// Then nil omits env, while a supplied map retains its exact value.
				require.NoError(t, err)
				var body map[string]json.RawMessage
				require.NoError(t, json.Unmarshal(fake.requests()[0].Body, &body))
				if env == nil {
					require.NotContains(t, body, "env")
				} else {
					expected, marshalErr := json.Marshal(env)
					require.NoError(t, marshalErr)
					require.JSONEq(t, string(expected), string(body["env"]))
				}
			})
		}
	}
}
