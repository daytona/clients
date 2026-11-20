// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestHarnessSkips_whenEnvironmentDisablesFeatures(t *testing.T) {
	for _, tt := range []struct {
		name string
		flag string
		test string
	}{
		{"build", "DAYTONA_SKIP_DOCKERFILE", "TestImagesBuildLifecycle"},
		{"context", "DAYTONA_SKIP_DOCKERFILE", "TestImagesBuildContext"},
		{"reader", "DAYTONA_SKIP_DOCKERFILE", "TestImagesBuildContextReader"},
		{"create", "DAYTONA_SKIP_DOCKERFILE", "TestCreateFromBuild"},
		{"toolbox", "DAYTONA_SKIP_TOOLBOX", "TestProcessLive"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a deployment that rejects requests and a disabled feature.
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusBadRequest)
			}))
			defer server.Close()
			executable, err := os.Executable()
			require.NoError(t, err)
			command := exec.CommandContext(t.Context(), executable, "-test.v", "-test.run=^"+tt.test+"$")
			command.Env = append(os.Environ(), "DAYTONA_API_KEY=harness-test",
				"DAYTONA_V2_API_URL="+server.URL, "DAYTONA_SKIP_DOCKERFILE=", "DAYTONA_SKIP_TOOLBOX=", tt.flag+"=1")
			// When running the real live scenario under the skip flag.
			output, err := command.CombinedOutput()
			// Then it is reported as skipped, never passed or failed.
			require.NoError(t, err, "%s", output)
			require.Contains(t, string(output), "--- SKIP: "+tt.test)
		})
	}
}

func TestHarnessBuild_whenSmokeImageIsConfigured(t *testing.T) {
	for _, test := range []string{"TestImagesBuildLifecycle", "TestCreateFromBuild"} {
		t.Run(test, func(t *testing.T) {
			// Given a build endpoint that records and rejects submission.
			dockerfiles := make(chan string, 1)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost && r.URL.Path == "/images" {
					var body struct {
						Dockerfile string `json:"dockerfile"`
					}
					if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
						t.Error(err)
					}
					dockerfiles <- body.Dockerfile
				}
				w.WriteHeader(http.StatusBadRequest)
			}))
			defer server.Close()
			executable, err := os.Executable()
			require.NoError(t, err)
			command := exec.CommandContext(t.Context(), executable, "-test.run=^"+test+"$")
			command.Env = append(os.Environ(), "DAYTONA_API_KEY=harness-test",
				"DAYTONA_V2_API_URL="+server.URL, "DAYTONA_SKIP_DOCKERFILE=", "DAYTONA_SKIP_TOOLBOX=",
				"SMOKE_IMAGE=python:3.13-slim")
			// When the live scenario submits its build to the wire-level fake.
			output, err := command.CombinedOutput()
			// Then its base comes from the override, not the hard-coded fallback.
			require.Error(t, err, "%s", output)
			select {
			case dockerfile := <-dockerfiles:
				require.True(t, strings.HasPrefix(dockerfile, "FROM python:3.13-slim\n"), dockerfile)
			default:
				t.Fatalf("build was not submitted: %s", output)
			}
		})
	}
}
