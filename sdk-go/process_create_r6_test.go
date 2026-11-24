// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestProcessRunPreservesID_whenCreateResponseMalformed(t *testing.T) {
	for _, operation := range []string{"exec", "session", "code"} {
		for _, streaming := range []bool{false, true} {
			for _, malformed := range []string{"kind", "state", "exitCode", "createdAt", "id", "missing id", "lossy id"} {
				t.Run(operation+"/"+map[bool]string{false: "collect", true: "stream"}[streaming]+"/"+malformed, func(t *testing.T) {
					// Given a successful create with one malformed response field.
					info := processFixture("p")
					switch malformed {
					case "kind":
						info["kind"] = 7
					case "state":
						info["state"] = 7
					case "exitCode":
						info["exitCode"] = "bad"
					case "createdAt":
						info["createdAt"] = "bad"
					case "id":
						info["id"] = ".."
					case "missing id":
						delete(info, "id")
					case "lossy id":
						info["id"] = "\ufffd"
					}
					sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
						"POST /processes": jsonHandler(t, info),
					})
					var writer io.Writer
					if streaming {
						writer = io.Discard
					}
					// When a blocking command parses the response.
					var result *ProcessResult
					var err error
					switch operation {
					case "exec":
						result, err = sandbox.Exec(t.Context(), "true", &ExecOptions{Stdout: writer})
					case "session":
						session, connectErr := sandbox.Process().ConnectSession("shell")
						require.NoError(t, connectErr)
						result, err = session.Run(t.Context(), "true", &SessionRunOptions{Stdout: writer})
					case "code":
						var code *RunCodeResult
						code, err = sandbox.RunCode(t.Context(), "1", &RunCodeOptions{Stdout: writer})
						if code != nil {
							result = &ProcessResult{ProcessID: code.Process.ProcessID, WaitResult: code.Process.WaitResult}
						}
					}
					// Then a validated identity survives, without claiming an outcome or making more requests.
					require.ErrorIs(t, err, ErrBadRequest)
					var sdkErr *Error
					require.ErrorAs(t, err, &sdkErr)
					require.Equal(t, SourceSDK, sdkErr.Source)
					require.Len(t, fake.requests(), 1)
					if malformed == "id" || malformed == "missing id" || malformed == "lossy id" {
						require.Nil(t, result)
						return
					}
					require.NotNil(t, result)
					require.Equal(t, "p", result.ProcessID)
					require.Equal(t, ProcessReasonUnknown, result.Reason)
					require.Nil(t, result.ExitCode)
					require.Empty(t, result.Output)
				})
			}
		}
	}
}
