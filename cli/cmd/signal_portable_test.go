// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestProcessWait_usesPortableStatus_whenSignalNumberDependsOnOS(t *testing.T) {
	for _, class := range []string{"linux", "macos"} {
		for _, signal := range []string{"USR1", "USR2", "BUS", "SYS", "CHLD", "CONT", "STOP", "TSTP", "URG", "IO"} {
			t.Run(class+"/"+signal, func(t *testing.T) {
				// Given a process ended by a signal with platform-specific numbering.
				// When waiting through the CLI's actual command surface.
				_, _, err := runToolboxCLI(t, []string{"sandbox", "process", "wait", "sbx", "p1"}, []toolboxExpectedRequest{
					{method: http.MethodGet, path: "/sandboxes/sbx", status: 200, auth: true, response: toolboxSandboxDetailFixture("sbx", class, "running")},
					{method: http.MethodGet, path: "/test-tools/sbx/processes/p1/wait", status: 200, auth: true, response: `{"reason":"signaled","signal":"SIG` + signal + `"}`},
				})
				// Then no host-specific signal number is invented.
				require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
				require.Equal(t, cerr.SourceProcess, cerr.DetailsOf(err).Source)
			})
		}
	}
}
