// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	stderrors "errors"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/common"
	"github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/types"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestNewSandboxConstruction(t *testing.T) {
	tests := []struct {
		name                string
		id                  string
		sandboxName         string
		state               apiclient.SandboxState
		target              string
		autoArchiveInterval int
		autoDeleteInterval  int
		networkBlockAll     bool
		networkAllowList    *string
	}{
		{
			name:                "basic construction",
			id:                  "test-id",
			sandboxName:         "test-name",
			state:               apiclient.SANDBOXSTATE_STARTED,
			target:              "us-east-1",
			autoArchiveInterval: 60,
			autoDeleteInterval:  -1,
			networkBlockAll:     false,
			networkAllowList:    nil,
		},
		{
			name:                "with network allow list",
			id:                  "id-2",
			sandboxName:         "sandbox-2",
			state:               apiclient.SANDBOXSTATE_STOPPED,
			target:              "eu-west-1",
			autoArchiveInterval: 0,
			autoDeleteInterval:  0,
			networkBlockAll:     true,
			networkAllowList:    strPtr("10.0.0.0/8"),
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			os.Clearenv()
			os.Setenv("DAYTONA_API_KEY", "test-api-key")

			client, err := NewClient()
			require.NoError(t, err)

			sandbox := newSandboxForTest(
				client,
				tt.id,
				tt.sandboxName,
				tt.state,
				tt.target,
				tt.autoArchiveInterval,
				tt.autoDeleteInterval,
				tt.networkBlockAll,
				tt.networkAllowList,
			)

			require.NotNil(t, sandbox)
			assert.Equal(t, tt.id, sandbox.ID)
			assert.Equal(t, tt.sandboxName, sandbox.Name)
			assert.Equal(t, tt.state, sandbox.State)
			assert.Equal(t, tt.target, sandbox.Target)
			assert.Equal(t, tt.autoArchiveInterval, sandbox.AutoArchiveInterval)
			assert.Equal(t, tt.autoDeleteInterval, sandbox.AutoDeleteInterval)
			require.NotNil(t, sandbox.NetworkBlockAll)
			assert.Equal(t, tt.networkBlockAll, *sandbox.NetworkBlockAll)
			assert.Equal(t, tt.networkAllowList, sandbox.NetworkAllowList)

			assert.NotNil(t, sandbox.FileSystem)
			assert.NotNil(t, sandbox.Git)
			assert.NotNil(t, sandbox.Process)
			assert.NotNil(t, sandbox.CodeInterpreter)
			assert.NotNil(t, sandbox.ComputerUse)
		})
	}
	os.Clearenv()
}

func TestSandboxStartTimeoutValidation(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STOPPED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doStartWithTimeout(ctx, -1*time.Second)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Timeout must be a non-negative number")

	os.Clearenv()
}

func TestSandboxStopTimeoutValidation(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doStopWithTimeout(ctx, -1*time.Second, false)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Timeout must be a non-negative number")

	os.Clearenv()
}

func TestSandboxDeleteTimeoutValidation(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STOPPED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doDeleteWithTimeout(ctx, -1*time.Second)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Timeout must be a non-negative number")

	os.Clearenv()
}

func TestSandboxWaitForStartTimeoutValidation(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_CREATING, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doWaitForStart(ctx, -1*time.Second)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Timeout must be a non-negative number")

	os.Clearenv()
}

func TestSandboxWaitForStopTimeoutValidation(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STOPPING, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doWaitForStop(ctx, -1*time.Second)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Timeout must be a non-negative number")

	os.Clearenv()
}

func TestSandboxSetAutoArchiveIntervalNil(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doSetAutoArchiveInterval(ctx, nil)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "intervalMinutes cannot be nil")

	os.Clearenv()
}

func TestSandboxSetAutoDeleteIntervalNil(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doSetAutoDeleteInterval(ctx, nil)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "intervalMinutes cannot be nil")

	os.Clearenv()
}

func TestSandboxResizeNilResources(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doResizeWithTimeout(ctx, nil, 60*time.Second)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Resources must not be nil")

	os.Clearenv()
}

func TestSandboxResizeTimeoutValidation(t *testing.T) {
	os.Clearenv()
	os.Setenv("DAYTONA_API_KEY", "test-api-key")

	client, err := NewClient()
	require.NoError(t, err)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err = sandbox.doResizeWithTimeout(ctx, &types.Resources{CPU: 2}, -1*time.Second)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "Timeout must be a non-negative number")

	os.Clearenv()
}

func TestSandboxStartAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "start failed"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STOPPED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err := sandbox.doStartWithTimeout(ctx, 5*time.Second)
	require.Error(t, err)

	os.Clearenv()
}

func TestSandboxStopAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "stop failed"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err := sandbox.doStopWithTimeout(ctx, 5*time.Second, false)
	require.Error(t, err)

	os.Clearenv()
}

func TestSandboxDeleteAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "forbidden"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STOPPED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err := sandbox.doDeleteWithTimeout(ctx, 5*time.Second)
	require.Error(t, err)

	os.Clearenv()
}

func TestSandboxSetLabelsAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "server error"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err := sandbox.doSetLabels(ctx, map[string]string{"env": "dev"})
	require.Error(t, err)

	os.Clearenv()
}

func TestSandboxArchiveAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "archive failed"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STOPPED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err := sandbox.doArchive(ctx)
	require.Error(t, err)

	os.Clearenv()
}

func newSandboxForTest(client *Client, id string, sandboxName string, state apiclient.SandboxState, target string, autoArchiveInterval int, autoDeleteInterval int, networkBlockAll bool, networkAllowList *string) *Sandbox {
	archive := float32(autoArchiveInterval)
	del := float32(autoDeleteInterval)
	dto := &apiclient.Sandbox{
		Id:                  id,
		Name:                sandboxName,
		Target:              target,
		State:               &state,
		NetworkBlockAll:     networkBlockAll,
		NetworkAllowList:    networkAllowList,
		AutoArchiveInterval: &archive,
		AutoDeleteInterval:  &del,
	}
	return NewSandbox(client, nil, dto, types.CodeLanguagePython, common.NewEventSubscriptionManager(nil))
}

// newSandboxForToolboxTest builds a minimal Sandbox connected to a real toolbox
// HTTP server for tests that exercise toolbox-API methods.
func newSandboxForToolboxTest(toolboxClient *toolbox.APIClient, id string, state apiclient.SandboxState) *Sandbox {
	dto := &apiclient.Sandbox{
		Id:    id,
		Name:  "name",
		State: &state,
	}
	return NewSandbox(nil, toolboxClient, dto, types.CodeLanguagePython, common.NewEventSubscriptionManager(nil))
}

func TestSandboxCreateLspServer(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	}))
	defer server.Close()

	sandbox := newSandboxForToolboxTest(createTestToolboxClient(server), "sb", apiclient.SANDBOXSTATE_STARTED)
	// Wire a non-nil otel state so we can assert it is propagated to the LSP
	// service — instrumentation that external callers cannot supply themselves
	// (otelState is unexported) is the reason this accessor exists.
	sandbox.otel = &otelState{}

	lsp := sandbox.CreateLspServer(types.LspLanguagePython, "/home/user/project")

	require.NotNil(t, lsp)
	require.Equal(t, types.LspLanguageID("python"), lsp.languageID)
	require.Equal(t, "/home/user/project", lsp.projectPath)
	require.Same(t, sandbox.ToolboxClient, lsp.toolboxClient)
	require.Same(t, sandbox.otel, lsp.otel)
}

func TestNextWaitForStatePollInterval(t *testing.T) {
	t.Run("polling mode stays fast for first five seconds", func(t *testing.T) {
		assert.Equal(t, waitForStatePollingInitialInterval, nextWaitForStatePollInterval(waitForStatePollingInitialInterval, 5*time.Second, false))
	})

	t.Run("polling mode backs off like origin main after five seconds", func(t *testing.T) {
		assert.Equal(t, 110*time.Millisecond, nextWaitForStatePollInterval(waitForStatePollingInitialInterval, 6*time.Second, false))
		assert.Equal(t, waitForStatePollingBackoffMax, nextWaitForStatePollInterval(waitForStatePollingBackoffMax, 10*time.Second, false))
	})

	t.Run("streaming mode keeps sparse one second safety polling", func(t *testing.T) {
		assert.Equal(t, waitForStateStreamingSafetyInterval, nextWaitForStatePollInterval(waitForStatePollingInitialInterval, 10*time.Second, true))
		assert.Equal(t, waitForStateStreamingSafetyInterval, nextWaitForStatePollInterval(waitForStateStreamingSafetyInterval, 10*time.Second, true))
	})
}

func TestSandboxRefreshDataSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		payload := testSandboxPayload("sb-1", "refreshed", apiclient.SANDBOXSTATE_STARTED)
		payload["target"] = "eu-west-1"
		payload["networkBlockAll"] = true
		payload["networkAllowList"] = "10.0.0.0/8"
		writeJSONResponse(t, w, http.StatusOK, payload)
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb-1", "before", apiclient.SANDBOXSTATE_CREATING, "us-east-1", 1, 2, false, nil)

	require.NoError(t, sandbox.RefreshData(context.Background()))
	assert.Equal(t, "refreshed", sandbox.Name)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.State)
	assert.Equal(t, "eu-west-1", sandbox.Target)
	require.NotNil(t, sandbox.NetworkBlockAll)
	assert.True(t, *sandbox.NetworkBlockAll)
	require.NotNil(t, sandbox.NetworkAllowList)
	assert.Equal(t, "10.0.0.0/8", *sandbox.NetworkAllowList)
}

func TestSandboxInfoMethods(t *testing.T) {
	t.Run("successfully gets user home and work dir", func(t *testing.T) {
		var calls int
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			calls++
			switch calls {
			case 1:
				writeJSONResponse(t, w, http.StatusOK, map[string]any{"dir": "/home/daytona"})
			case 2:
				writeJSONResponse(t, w, http.StatusOK, map[string]any{"dir": "/workspace"})
			default:
				http.NotFound(w, r)
			}
		}))
		defer server.Close()

		sandbox := newSandboxForToolboxTest(createTestToolboxClient(server), "sb", apiclient.SANDBOXSTATE_STARTED)
		home, err := sandbox.GetUserHomeDir(context.Background())
		require.NoError(t, err)
		workdir, err := sandbox.GetWorkingDir(context.Background())
		require.NoError(t, err)
		assert.Equal(t, "/home/daytona", home)
		assert.Equal(t, "/workspace", workdir)
	})

	t.Run("converts toolbox errors", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			writeJSONResponse(t, w, http.StatusInternalServerError, map[string]string{"message": "boom"})
		}))
		defer server.Close()

		sandbox := newSandboxForToolboxTest(createTestToolboxClient(server), "sb", apiclient.SANDBOXSTATE_STARTED)
		_, err := sandbox.GetUserHomeDir(context.Background())
		require.Error(t, err)
		_, err = sandbox.GetWorkingDir(context.Background())
		require.Error(t, err)
	})
}

func TestSandboxLifecycleSuccessPaths(t *testing.T) {
	t.Run("start stop and delete succeed", func(t *testing.T) {
		var getCount int
		var deleted bool
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch r.Method {
			case http.MethodPost:
				writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_STARTING))
			case http.MethodGet:
				if deleted {
					writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_DESTROYED))
					return
				}
				getCount++
				state := apiclient.SANDBOXSTATE_STARTED
				if getCount > 1 {
					state = apiclient.SANDBOXSTATE_STOPPED
				}
				writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", state))
			case http.MethodDelete:
				deleted = true
				w.WriteHeader(http.StatusOK)
			default:
				w.WriteHeader(http.StatusOK)
			}
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STOPPED, "us", 0, -1, false, nil)
		require.NoError(t, sandbox.doStartWithTimeout(context.Background(), 1500*time.Millisecond))
		require.NoError(t, sandbox.doStopWithTimeout(context.Background(), 1500*time.Millisecond, true))
		require.NoError(t, sandbox.doDeleteWithTimeout(context.Background(), 1500*time.Millisecond))
	})

	t.Run("wait for start returns sandbox error state", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			payload := testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_ERROR)
			payload["errorReason"] = "failed"
			writeJSONResponse(t, w, http.StatusOK, payload)
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTING, "us", 0, -1, false, nil)
		err := sandbox.doWaitForStart(context.Background(), 1500*time.Millisecond)
		require.Error(t, err)
		assert.Contains(t, err.Error(), "sandbox entered error state")
		var daytonaErr *errors.DaytonaError
		assert.False(t, stderrors.As(err, &daytonaErr), "error-state failures must not be wrapped in DaytonaError")
	})
}

func TestSandboxPreviewAndLabelOperations(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPut:
			writeJSONResponse(t, w, http.StatusOK, map[string]any{"labels": map[string]string{"env": "test"}})
		case http.MethodGet:
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_STARTED))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
	require.NoError(t, sandbox.SetLabels(context.Background(), map[string]string{"env": "test"}))
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.State)
}

func TestSandboxExperimentalOperations(t *testing.T) {
	t.Run("fork succeeds and waits for start", func(t *testing.T) {
		var getCount int
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch r.Method {
			case http.MethodPost:
				writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("forked", "forked-name", apiclient.SANDBOXSTATE_STARTING))
			case http.MethodGet:
				getCount++
				state := apiclient.SANDBOXSTATE_STARTING
				if getCount > 1 {
					state = apiclient.SANDBOXSTATE_STARTED
				}
				writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("forked", "forked-name", state))
			default:
				w.WriteHeader(http.StatusOK)
			}
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
		forked, err := sandbox.ExperimentalForkWithTimeout(context.Background(), strPtr("forked-name"), 3*time.Second)
		require.NoError(t, err)
		assert.Equal(t, "forked", forked.ID)
	})

	t.Run("create snapshot waits until snapshotting finishes", func(t *testing.T) {
		var getCount int
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch r.Method {
			case http.MethodPost:
				w.WriteHeader(http.StatusOK)
			case http.MethodGet:
				getCount++
				state := apiclient.SANDBOXSTATE_SNAPSHOTTING
				if getCount > 1 {
					state = apiclient.SANDBOXSTATE_STARTED
				}
				writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", state))
			default:
				w.WriteHeader(http.StatusOK)
			}
		}))
		defer server.Close()

		client := createTestClientWithServer(t, server)
		sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
		require.NoError(t, sandbox.ExperimentalCreateSnapshotWithTimeout(context.Background(), "snap-name", 2*time.Second))
	})
}

func TestSandboxResizeFlow(t *testing.T) {
	var getCount int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			getCount++
			state := apiclient.SANDBOXSTATE_RESIZING
			if getCount > 1 {
				state = apiclient.SANDBOXSTATE_STARTED
			}
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", state))
		default:
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_RESIZING))
		}
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
	require.NoError(t, sandbox.ResizeWithTimeout(context.Background(), &types.Resources{CPU: 2, Memory: 2048, Disk: 10}, 3*time.Second))
}

func TestSandboxUpdateSecrets(t *testing.T) {
	var body map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodPut, r.Method)
		assert.Contains(t, r.URL.Path, "/secrets")
		require.NoError(t, json.NewDecoder(r.Body).Decode(&body))
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "refreshed-name", apiclient.SANDBOXSTATE_STARTED))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STOPPED, "us", 0, -1, false, nil)

	ctx := context.Background()
	require.NoError(t, sandbox.UpdateSecrets(ctx, map[string]string{"FOO": "foo-secret"}))
	assert.Equal(t, map[string]any{"secrets": []any{map[string]any{"FOO": "foo-secret"}}}, body)
	// The sandbox is refreshed from the response DTO.
	assert.Equal(t, "refreshed-name", sandbox.Name)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.State)

	// An empty map must send an explicit empty array (detaches all secrets),
	// not omit the required field.
	require.NoError(t, sandbox.UpdateSecrets(ctx, map[string]string{}))
	assert.Equal(t, map[string]any{"secrets": []any{}}, body)

	// A nil map is rejected so an uninitialized map can't silently detach all secrets.
	require.Error(t, sandbox.UpdateSecrets(ctx, nil))
}

func TestSandboxUpdateSecretsAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": "server error"})
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)

	sandbox := newSandboxForTest(client, "test-id", "test", apiclient.SANDBOXSTATE_STARTED, "us-east-1", 60, -1, false, nil)

	ctx := context.Background()
	err := sandbox.doUpdateSecrets(ctx, map[string]string{"FOO": "foo-secret"})
	require.Error(t, err)
}

func TestSandboxUpdateEnv(t *testing.T) {
	var body map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodPost, r.Method)
		assert.Contains(t, r.URL.Path, "/env")
		require.NoError(t, json.NewDecoder(r.Body).Decode(&body))
		// The daemon responds with a status message, not the resulting environment.
		writeJSONResponse(t, w, http.StatusOK, map[string]string{"message": "Environment updated successfully"})
	}))
	defer server.Close()

	sandbox := newSandboxForToolboxTest(createTestToolboxClient(server), "sb", apiclient.SANDBOXSTATE_STARTED)

	require.NoError(t, sandbox.UpdateEnv(context.Background(), map[string]string{"FOO": "bar"}, []string{"OLD_VAR"}))

	assert.Equal(t, map[string]any{"FOO": "bar"}, body["set"])
	assert.Equal(t, []any{"OLD_VAR"}, body["unset"])
	// UnsetValuePrefix is an internal reconciliation knob and must never be sent.
	assert.NotContains(t, body, "unsetValuePrefix")
}

func TestWaitForStateCachedErrorRecoversAfterRefresh(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_STARTED))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_ERROR, "us", 0, -1, false, nil)

	err := sandbox.waitForState(
		context.Background(),
		[]apiclient.SandboxState{apiclient.SANDBOXSTATE_STARTED},
		[]apiclient.SandboxState{apiclient.SANDBOXSTATE_ERROR, apiclient.SANDBOXSTATE_BUILD_FAILED},
		false,
	)
	require.NoError(t, err)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.State)
}

func TestWaitForStateNearZeroTimeoutRefreshes(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_STARTED))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_CREATING, "us", 0, -1, false, nil)

	err := sandbox.doWaitForStart(context.Background(), 50*time.Millisecond)
	require.NoError(t, err)
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.State)
}

func TestPauseLandingInStoppedResolves(t *testing.T) {
	var getCount int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_PAUSING))
		case http.MethodGet:
			getCount++
			state := apiclient.SANDBOXSTATE_PAUSING
			if getCount > 1 {
				state = apiclient.SANDBOXSTATE_STOPPED
			}
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", state))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
	err := sandbox.doPauseWithTimeout(context.Background(), 2*time.Second)
	require.NoError(t, err)
	assert.Equal(t, apiclient.SANDBOXSTATE_STOPPED, sandbox.State)
}

func TestDeleteFireAndForget(t *testing.T) {
	var getCount int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodDelete:
			w.WriteHeader(http.StatusOK)
		case http.MethodGet:
			getCount++
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_DESTROYING))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
	err := sandbox.doDeleteWithTimeout(context.Background(), 5*time.Second)
	require.NoError(t, err)
	assert.Equal(t, 0, getCount, "Delete must not poll for state")
}

func TestDeleteAndWaitBlocksUntilDestroyed(t *testing.T) {
	var getCount int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodDelete:
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_DESTROYING))
		case http.MethodGet:
			getCount++
			state := apiclient.SANDBOXSTATE_DESTROYING
			if getCount > 1 {
				state = apiclient.SANDBOXSTATE_DESTROYED
			}
			writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", state))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
	err := sandbox.doDeleteAndWait(context.Background(), 3*time.Second)
	require.NoError(t, err)
	assert.Equal(t, apiclient.SANDBOXSTATE_DESTROYED, sandbox.State)
	assert.Greater(t, getCount, 0, "DeleteAndWait must poll for state")
}

func TestPauseTimeoutReturnsDaytonaTimeoutError(t *testing.T) {
	// Server always returns PAUSING — pause never completes.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_PAUSING))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)
	err := sandbox.doPauseWithTimeout(context.Background(), 200*time.Millisecond)
	require.Error(t, err)

	require.ErrorIs(t, err, errors.ErrTimeout, "pause timeout must match ErrTimeout, got %T", err)
	assert.Contains(t, err.Error(), "pause")
}

func TestPauseCancellationIsNotReportedAsTimeout(t *testing.T) {
	// Server always returns PAUSING — pause never completes on its own.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_PAUSING))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTED, "us", 0, -1, false, nil)

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(150 * time.Millisecond)
		cancel()
	}()
	err := sandbox.doPauseWithTimeout(ctx, 30*time.Second)
	require.Error(t, err)

	require.False(t, stderrors.Is(err, errors.ErrTimeout), "explicit cancellation must not match ErrTimeout, got %v", err)
	require.ErrorIs(t, err, context.Canceled)
}

func TestWaitForStartCancellationIsNotReportedAsTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", apiclient.SANDBOXSTATE_STARTING))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_STARTING, "us", 0, -1, false, nil)

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(150 * time.Millisecond)
		cancel()
	}()
	err := sandbox.doWaitForStart(ctx, 30*time.Second)
	require.Error(t, err)

	require.False(t, stderrors.Is(err, errors.ErrTimeout), "explicit cancellation must not match ErrTimeout, got %v", err)
	require.ErrorIs(t, err, context.Canceled)
}

func TestWaitForStateFinalRefreshWithFreshContext(t *testing.T) {
	// First GET returns non-target so the t=0 poll doesn't short-circuit;
	// subsequent GETs return the target state for the final refresh.
	var getCount int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := int(atomic.AddInt32(&getCount, 1))
		state := apiclient.SANDBOXSTATE_CREATING
		if n > 1 {
			state = apiclient.SANDBOXSTATE_STARTED
		}
		writeJSONResponse(t, w, http.StatusOK, testSandboxPayload("sb", "sandbox", state))
	}))
	defer server.Close()

	client := createTestClientWithServer(t, server)
	sandbox := newSandboxForTest(client, "sb", "sandbox", apiclient.SANDBOXSTATE_CREATING, "us", 0, -1, false, nil)

	// 50ms timeout expires before the 100ms poll interval, forcing ctx.Done().
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	err := sandbox.waitForState(
		ctx,
		[]apiclient.SandboxState{apiclient.SANDBOXSTATE_STARTED},
		[]apiclient.SandboxState{apiclient.SANDBOXSTATE_ERROR},
		false,
	)
	require.NoError(t, err, "final refresh on fresh context must observe target state")
	assert.Equal(t, apiclient.SANDBOXSTATE_STARTED, sandbox.State)
}
