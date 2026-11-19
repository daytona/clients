// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	api "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/options"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func gangResponse(proxy string) map[string]any {
	dto := testSandboxPayload("head", "training", api.SANDBOXSTATE_STARTED)
	dto["gpu"], dto["nodeCount"], dto["topologyGeneration"], dto["toolboxProxyUrl"] = 16, 2, 7, proxy
	dto["nodes"] = []api.SandboxNode{
		{Id: "head", Rank: 0, State: api.SANDBOXSTATE_STARTED, Incarnation: 1, Cpu: 16, Gpu: 8, Memory: 192, Disk: 512},
		{Id: "worker", Rank: 1, State: api.SANDBOXSTATE_STARTED, Incarnation: 2, Cpu: 32, Gpu: 8, Memory: 256, Disk: 512},
	}
	return dto
}

func TestMultiNodeCreateAndMembership(t *testing.T) {
	var gets atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			var body map[string]any
			if !assert.NoError(t, json.NewDecoder(r.Body).Decode(&body)) {
				return
			}
			assert.Equal(t, []any{float64(16), float64(32)}, body["cpuPerNode"])
			assert.Equal(t, float64(192), body["memory"])
			assert.NotContains(t, body, "cpu")
		} else {
			gets.Add(1)
			assert.Equal(t, "/sandbox/head", r.URL.Path)
		}
		writeJSONResponse(t, w, http.StatusOK, gangResponse("http://"+r.Host+"/toolbox"))
	}))
	defer server.Close()
	client := createTestClientWithServer(t, server)
	params := types.MultiNodeImageParams{ImageParams: types.ImageParams{Image: "ubuntu", Resources: &types.Resources{GPU: 16, Memory: 192, GpuType: []types.GpuType{api.GPUTYPE_H100}}}, CPUPerNode: []int32{16, 32}}
	sandbox, err := client.Create(t.Context(), &params, options.WithWaitForStart(false))
	require.NoError(t, err)
	require.EqualValues(t, 0, gets.Load())
	nodes, err := sandbox.Nodes(t.Context())
	require.NoError(t, err)
	require.EqualValues(t, 1, gets.Load())
	require.Len(t, nodes.All(), 2)
	worker, err := nodes.Get(1)
	require.NoError(t, err)
	require.Equal(t, "worker", worker.Id)
	require.EqualValues(t, 2, worker.Incarnation)
	require.EqualValues(t, 7, nodes.TopologyGeneration())
	for _, values := range [][]int32{{}, {3}, {0, 4}} {
		params.CPUPerNode = values
		_, err := client.Create(t.Context(), params)
		require.Error(t, err)
	}
	params.CPUPerNode = []int32{2, 4}
	params.Resources.CPU = 2
	_, err = client.Create(t.Context(), params)
	require.Error(t, err)
	params.Resources.CPU = 0
	params.Resources.GpuType = nil
	_, err = client.Create(t.Context(), params)
	require.ErrorContains(t, err, "GPU type")
}

func TestNodesSparseAndDirectWorker(t *testing.T) {
	dto := &api.Sandbox{Id: "head", Gpu: 16, AdditionalProperties: map[string]any{"nodeCount": 2}}
	sandbox := &Sandbox{client: &Client{}}
	_, err := sandbox.nodesFromDTO(dto)
	require.ErrorContains(t, err, "unavailable")
	dto.Id = "worker"
	dto.AdditionalProperties = map[string]any{"headSandboxId": "head", "nodeRank": 1, "incarnation": 3}
	nodes, err := sandbox.nodesFromDTO(dto)
	require.NoError(t, err)
	worker, err := nodes.Get(1)
	require.NoError(t, err)
	require.EqualValues(t, 3, worker.Incarnation)
	_, err = nodes.Get(0)
	require.Error(t, err)
	dto.Gpu = 0
	dto.AdditionalProperties = nil
	nodes, err = sandbox.nodesFromDTO(dto)
	require.NoError(t, err)
	require.Len(t, nodes.All(), 1)
	_, err = nodes.Add(t.Context(), []NodeResources{{1, 1, 1}})
	require.ErrorContains(t, err, "head")
	dto.AdditionalProperties = map[string]any{"nodeCount": 0}
	_, err = sandbox.nodesFromDTO(dto)
	require.ErrorContains(t, err, "node count")
}

func TestNodesApplyMembershipAtObservedGeneration(t *testing.T) {
	for _, action := range []string{"add", "release", "replace", "stale", "cancel", "failed", "poll"} {
		t.Run(action, func(t *testing.T) {
			var posts atomic.Int32
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, "Bearer test-api-key", r.Header.Get("Authorization"))
				if r.Method == http.MethodGet && r.URL.Path == "/sandbox/head" {
					writeJSONResponse(t, w, 200, gangResponse("http://"+r.Host))
					return
				}
				op := *api.NewGangMembershipOperation("operation", "head", "ADD", "COMPLETED", "COMMIT", 7, 8, 1, "now", "now")
				if r.Method == http.MethodPost {
					posts.Add(1)
					var body map[string]any
					if !assert.NoError(t, json.NewDecoder(r.Body).Decode(&body)) {
						return
					}
					assert.Equal(t, float64(7), body["expectedGeneration"])
					switch action {
					case "release":
						assert.Equal(t, "/sandbox/head/nodes/remove", r.URL.Path)
						assert.Equal(t, float64(1), body["count"])
					case "replace":
						assert.Equal(t, "/sandbox/head/nodes/1/replace", r.URL.Path)
					default:
						assert.Equal(t, "/sandbox/head/nodes", r.URL.Path)
						assert.Len(t, body["nodes"], 1)
					}
					if action == "stale" {
						http.Error(w, "stale generation", http.StatusConflict)
						return
					}
					if action == "poll" || action == "cancel" {
						op.Status = "RUNNING"
					}
					if action == "failed" {
						op.Status = "FAILED"
						op.SetErrorReason("capacity unavailable")
					}
				} else {
					assert.Equal(t, "/sandbox/head/membership-operations/operation", r.URL.Path)
				}
				writeJSONResponse(t, w, 200, op)
			}))
			defer server.Close()
			s := &Sandbox{ID: "head", client: createTestClientWithServer(t, server)}
			ns, err := s.Nodes(ctx)
			require.NoError(t, err)
			var op *api.GangMembershipOperation
			switch action {
			case "release":
				op, err = ns.Release(ctx, 1)
			case "replace":
				op, err = ns.Replace(ctx, 1)
			case "cancel":
				op, err = ns.Add(ctx, []NodeResources{{1, 2, 3}}, 150*time.Millisecond)
			default:
				op, err = ns.Add(ctx, []NodeResources{{1, 2, 3}})
			}
			require.EqualValues(t, 1, posts.Load())
			switch action {
			case "stale":
				require.Error(t, err)
				require.Nil(t, op)
			case "cancel":
				require.ErrorIs(t, err, context.DeadlineExceeded)
				require.Equal(t, "operation", op.Id)
			case "failed":
				require.ErrorContains(t, err, "capacity unavailable")
				require.Equal(t, "operation", op.Id)
			default:
				require.NoError(t, err)
				require.Equal(t, "COMPLETED", op.Status)
			}
		})
	}
}

func TestNodeDataPlaneUsesAddressedMember(t *testing.T) {
	var uploaded atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer test-api-key", r.Header.Get("Authorization"))
		if r.URL.Path == "/sandbox/head" {
			writeJSONResponse(t, w, 200, gangResponse("http://"+r.Host+"/toolbox"))
			return
		}
		if strings.HasSuffix(r.URL.Path, "/process/execute") {
			var body map[string]any
			if !assert.NoError(t, json.NewDecoder(r.Body).Decode(&body)) {
				return
			}
			if body["command"] == rdmaUserspaceCheck {
				writeJSONResponse(t, w, 200, map[string]any{"exitCode": 1, "result": ""})
				return
			}
			if r.URL.Path == "/toolbox/worker/process/execute" {
				http.Error(w, "worker offline", http.StatusServiceUnavailable)
				return
			}
			assert.Equal(t, "/toolbox/head/process/execute", r.URL.Path)
			writeJSONResponse(t, w, 200, map[string]any{"exitCode": 0, "result": "head output"})
			return
		}
		assert.True(t, strings.HasPrefix(r.URL.Path, "/toolbox/head/") || strings.HasPrefix(r.URL.Path, "/toolbox/worker/"))
		f, _, err := r.FormFile("files[0].file")
		if !assert.NoError(t, err) {
			return
		}
		defer f.Close()
		content, err := io.ReadAll(f)
		if !assert.NoError(t, err) {
			return
		}
		assert.Equal(t, "shared contents", string(content))
		if r.FormValue("files[0].path") == "/tmp/partial" && strings.HasPrefix(r.URL.Path, "/toolbox/worker/") {
			http.Error(w, "worker offline", http.StatusServiceUnavailable)
			return
		}
		uploaded.Add(1)
	}))
	defer server.Close()
	s := &Sandbox{ID: "head", client: createTestClientWithServer(t, server)}
	ns, err := s.Nodes(t.Context())
	require.NoError(t, err)
	results, err := ns.ExecuteCommand(t.Context(), "echo hello")
	var failure *NodeErrors
	require.True(t, errors.As(err, &failure))
	require.Contains(t, failure.Failures, int32(1))
	require.Equal(t, "head output", results[0].Result)
	require.NotEmpty(t, ns.All()[0].Warnings())
	require.NoError(t, ns.UploadFile(t.Context(), []byte("shared contents"), "/tmp/shared"))
	path := filepath.Join(t.TempDir(), "shared.txt")
	require.NoError(t, os.WriteFile(path, []byte("shared contents"), 0600))
	require.NoError(t, ns.UploadFile(t.Context(), path, "/tmp/shared"))
	require.EqualValues(t, 4, uploaded.Load())
	err = ns.UploadFile(t.Context(), path, "/tmp/partial")
	require.ErrorAs(t, err, &failure)
	require.Len(t, failure.Failures, 1)
	require.Contains(t, failure.Failures, int32(1))
	require.EqualValues(t, 5, uploaded.Load())
}

func TestNodeUploadRejectsInvalidSourceBeforeUpload(t *testing.T) {
	nodes := &SandboxNodes{members: []*SandboxNode{
		{SandboxNode: api.SandboxNode{Rank: 0}},
		{SandboxNode: api.SandboxNode{Rank: 1}},
	}}
	var rankErrors *NodeErrors
	err := nodes.UploadFile(t.Context(), 42, "/tmp/shared")
	require.ErrorContains(t, err, "local path or byte slice")
	require.False(t, errors.As(err, &rankErrors))

	err = nodes.UploadFile(t.Context(), filepath.Join(t.TempDir(), "missing"), "/tmp/shared")
	require.ErrorIs(t, err, os.ErrNotExist)
	require.False(t, errors.As(err, &rankErrors))

	err = nodes.UploadFile(t.Context(), t.TempDir(), "/tmp/shared")
	require.ErrorContains(t, err, "directory")
	require.False(t, errors.As(err, &rankErrors))
}

func TestNodeUploadUsesValidatedFileLength(t *testing.T) {
	source, err := os.Create(filepath.Join(t.TempDir(), "shared.txt"))
	require.NoError(t, err)
	defer source.Close()
	_, err = source.WriteString("original")
	require.NoError(t, err)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		file, _, err := r.FormFile("files[0].file")
		if !assert.NoError(t, err) {
			return
		}
		defer file.Close()
		data, err := io.ReadAll(file)
		assert.NoError(t, err)
		want := "original"
		if r.FormValue("files[0].path") == "/empty" {
			want = ""
		}
		assert.Equal(t, want, string(data))
	}))
	defer server.Close()
	client := createTestToolboxClient(server)
	client.GetConfig().HTTPClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		// The source has been validated, but this request's body has not been read.
		if _, err := source.WriteString(" appended"); err != nil {
			return nil, err
		}
		return server.Client().Transport.RoundTrip(req)
	})}
	fs := NewFileSystemService(client, nil)
	nodes := &SandboxNodes{members: []*SandboxNode{
		{SandboxNode: api.SandboxNode{Rank: 0}, FileSystem: fs},
		{SandboxNode: api.SandboxNode{Rank: 1}, FileSystem: fs},
	}}
	require.NoError(t, nodes.UploadFile(t.Context(), source.Name(), "/shared"))
	require.NoError(t, source.Truncate(0))
	_, err = source.Seek(0, io.SeekStart)
	require.NoError(t, err)
	require.NoError(t, nodes.UploadFile(t.Context(), source.Name(), "/empty"))
}

func TestNodePreviewAndSSHUseWorker(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer test-api-key", r.Header.Get("Authorization"))
		switch {
		case r.URL.Path == "/sandbox/head":
			writeJSONResponse(t, w, 200, gangResponse("http://"+r.Host))
		case r.Method == http.MethodGet && r.URL.Path == "/sandbox/worker/ports/8080/preview-url":
			writeJSONResponse(t, w, 200, api.PortPreviewUrl{SandboxId: "worker", Url: "https://preview.example.com", Token: "preview"})
		case r.Method == http.MethodPost && r.URL.Path == "/sandbox/worker/ssh-access":
			writeJSONResponse(t, w, 200, api.SshAccessDto{Id: "access", SandboxId: "worker", Token: "ssh", SshCommand: "ssh example"})
		case r.Method == http.MethodDelete && r.URL.Path == "/sandbox/worker/ssh-access":
			assert.Equal(t, "ssh", r.URL.Query().Get("token"))
			writeJSONResponse(t, w, 200, testSandboxPayload("worker", "training", api.SANDBOXSTATE_STARTED))
		default:
			t.Errorf("unexpected %s %s", r.Method, r.URL)
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	s := &Sandbox{ID: "head", client: createTestClientWithServer(t, server)}
	nodes, err := s.Nodes(t.Context())
	require.NoError(t, err)
	worker, err := nodes.Get(1)
	require.NoError(t, err)
	preview, err := worker.GetPreviewLink(t.Context(), 8080)
	require.NoError(t, err)
	require.Equal(t, "preview", preview.Token)
	access, err := worker.CreateSSHAccess(t.Context(), nil)
	require.NoError(t, err)
	require.Equal(t, "worker", access.SandboxId)
	require.NoError(t, worker.RevokeSSHAccess(t.Context(), access.Token))
}
