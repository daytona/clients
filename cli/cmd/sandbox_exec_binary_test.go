// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/base64"
	"fmt"
	"net/http"
	"net/url"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestExec_preservesBinaryChannels_whenLogAlphabetOrPaddingChanges(t *testing.T) {
	// Given
	payload := []byte{0xfb, 0xff, 0xfe, 0, 'a', 0x80, 1}
	for _, encoding := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		encoded := encoding.EncodeToString(payload)
		t.Run(encoded, func(t *testing.T) {
			// When
			stdout, stderr, err := runToolboxCLI(t, []string{"sandbox", "exec", "sbx", "--execution-timeout", "1500us", "--", "binary"}, []toolboxExpectedRequest{
				{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
				{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 201, auth: true, query: url.Values{"logs": {"base64"}, "waitMs": {"1000"}}, response: fmt.Sprintf(`{"id":"proc","state":"terminal","kind":"exec","stdin":"closed","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"exited","exitCode":0,"logs":{"frames":[{"channel":"stdout","cursor":"c1","data":%q,"timestamp":1},{"channel":"stderr","cursor":"c2","data":%q,"timestamp":1}],"nextCursor":"c2","truncatedHead":false,"eof":true}}`, encoded, encoded), check: func(t *testing.T, _ *http.Request, body []byte) {
					request := mustJSONMap(t, string(body))
					assert.Equal(t, float64(2), request["timeoutMs"])
				}},
			})
			// Then
			require.NoError(t, err)
			require.Equal(t, payload, []byte(stdout))
			require.Equal(t, payload, []byte(stderr))
		})
	}
}

func TestExec_returnsShellStartFailure_whenCommandCannotStart(t *testing.T) {
	// Given / When
	_, _, err := runToolboxCLI(t, []string{"sandbox", "exec", "sbx", "--", "missing"}, []toolboxExpectedRequest{
		{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", "linux", "running")},
		{method: http.MethodPost, path: "/test-tools/sbx/processes", status: 400, auth: true, response: `{"code":"PROCESS_START_FAILED","source":"DAYTONA_DAEMON","message":"cannot start command"}`},
	})
	// Then
	details := cerr.DetailsOf(err)
	require.Equal(t, 127, details.ExitCode)
	require.Equal(t, cerr.SourceProcess, details.Source)
	require.Equal(t, "PROCESS_START_FAILED", details.Code)
}

func TestExec_refusesExecutionTimeout_whenNotPositive(t *testing.T) {
	for _, budget := range []string{"0s", "-1s"} {
		t.Run(budget, func(t *testing.T) {
			// Given / When
			_, _, code := run(t, cfgDir(t), nil, "sandbox", "exec", "sbx", "--execution-timeout", budget, "--", "true")
			// Then
			require.Equal(t, cerr.ExitUsage, code)
		})
	}
}
