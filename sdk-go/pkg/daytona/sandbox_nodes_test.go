// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	stderrors "errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"github.com/gorilla/websocket"
	"github.com/stretchr/testify/require"
)

func nodesTestClient(server *httptest.Server) *Client {
	cfg := apiclient.NewConfiguration()
	cfg.HTTPClient = server.Client()
	cfg.Servers = apiclient.ServerConfigurations{{URL: server.URL + "/api"}}
	return &Client{apiKey: "test-api-key", httpClient: server.Client(), apiClient: apiclient.NewAPIClient(cfg)}
}

func nodesTestDTO(proxy string) *apiclient.Sandbox {
	state := apiclient.SANDBOXSTATE_STARTED
	return &apiclient.Sandbox{
		Id: "head", Name: "gang", State: &state, Cpu: 32, Gpu: 16, Memory: 384, Disk: 1024,
		ToolboxProxyUrl: proxy, Labels: map[string]string{}, NodeCount: apiclient.PtrInt32(2), TopologyGeneration: apiclient.PtrInt32(7),
		Nodes: []apiclient.SandboxNode{
			{Rank: 0, Id: "head", State: state, Incarnation: 1, Cpu: 16, Gpu: 8, Memory: 192, Disk: 512},
			{Rank: 1, Id: "worker", State: state, Incarnation: 1, Cpu: 16, Gpu: 8, Memory: 192, Disk: 512},
		},
	}
}

func nodesTestSandbox(t *testing.T, server *httptest.Server, dto *apiclient.Sandbox) *Sandbox {
	t.Helper()
	client := nodesTestClient(server)
	tb, err := client.createToolboxClient(dto.ToolboxProxyUrl, dto.Id)
	require.NoError(t, err)
	return NewSandbox(client, tb, dto, types.CodeLanguagePython, nil)
}

func writeNodesJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(value)
}

func TestSandboxNodesPreserveHandlesAndRefreshRouting(t *testing.T) {
	for _, full := range []bool{false, true} {
		t.Run(map[bool]string{false: "partial", true: "full"}[full], func(t *testing.T) {
			var paths []string
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				paths = append(paths, r.URL.Path)
				require.Equal(t, "Bearer test-api-key", r.Header.Get("Authorization"))
				writeNodesJSON(w, map[string]any{"exitCode": 0, "result": "worker"})
			}))
			defer server.Close()
			dto := nodesTestDTO(server.URL + "/old")
			sandbox := nodesTestSandbox(t, server, dto)
			head, err := sandbox.Nodes.Get(0)
			require.NoError(t, err)
			worker, err := sandbox.Nodes.Get(1)
			require.NoError(t, err)
			process := worker.Process
			dto.ToolboxProxyUrl = server.URL + "/new"
			if !full {
				dto.Nodes = nil
			}
			sandbox.updateFromAPIResponse(dto)
			nodes := sandbox.Nodes.All()
			require.Same(t, head, nodes[0])
			require.Same(t, worker, nodes[1])
			require.Same(t, process, worker.Process)
			_, err = process.ExecuteCommand(context.Background(), "echo worker")
			require.NoError(t, err)
			require.Equal(t, []string{"/new/worker/process/execute"}, paths)
			require.Contains(t, head.toolboxClient.GetConfig().Servers[0].URL, "/new/head")
		})
	}
}

func TestSandboxNodesRejectInvalidMetadata(t *testing.T) {
	server := httptest.NewServer(http.NotFoundHandler())
	defer server.Close()
	for _, mutate := range []func(*apiclient.Sandbox){
		func(d *apiclient.Sandbox) { d.Nodes[0].Id = "worker" },
		func(d *apiclient.Sandbox) { d.Nodes[1].Rank = 3 },
		func(d *apiclient.Sandbox) { d.Nodes[1].Id = ".." },
		func(d *apiclient.Sandbox) { d.Nodes[1].Id = "head" },
	} {
		dto := nodesTestDTO(server.URL)
		mutate(dto)
		sandbox := nodesTestSandbox(t, server, dto)
		require.Error(t, sandbox.Nodes.Err())
		_, err := sandbox.Nodes.Get(0)
		require.Error(t, err)
	}
}

func TestSandboxNodesRefreshGenerationAndMembership(t *testing.T) {
	var server *httptest.Server
	var gets int
	var request apiclient.AddGangNodes
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet:
			gets++
			dto := nodesTestDTO(server.URL + "/toolbox")
			if gets == 2 {
				dto.TopologyGeneration = apiclient.PtrInt32(8)
				dto.NodeCount = apiclient.PtrInt32(3)
				dto.Nodes = append(dto.Nodes, apiclient.SandboxNode{Rank: 2, Id: "third", State: apiclient.SANDBOXSTATE_STARTED, Incarnation: 1, Gpu: 8})
			}
			writeNodesJSON(w, dto)
		case r.Method == http.MethodPost:
			require.Equal(t, "/api/sandbox/head/nodes", r.URL.Path)
			require.NoError(t, json.NewDecoder(r.Body).Decode(&request))
			writeNodesJSON(w, &apiclient.GangMembershipOperation{Id: "op", SandboxId: "head", Status: "COMPLETED", Type: "ADD"})
		}
	}))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	original, _ := sandbox.Nodes.Get(1)
	operation, err := sandbox.Nodes.Add(context.Background(), []NodeResources{{CPU: 16, Memory: 192, Disk: 512}}, time.Second)
	require.NoError(t, err)
	require.Equal(t, "COMPLETED", operation.Status)
	require.Equal(t, int32(7), request.ExpectedGeneration)
	require.Len(t, request.Nodes, 1)
	require.Len(t, sandbox.Nodes.All(), 3)
	require.Equal(t, 3, sandbox.NodeCount)
	worker, _ := sandbox.Nodes.Get(1)
	require.Same(t, original, worker)
}

func TestSandboxNodesMembershipTimeoutIncludesInitialRefresh(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests.Add(1); <-r.Context().Done() }))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	_, err := sandbox.Nodes.Replace(context.Background(), 1, 30*time.Millisecond)
	require.Error(t, err)
	require.Equal(t, int32(1), requests.Load())
}

func TestSandboxNodesExecuteTimeoutDestroysEverySession(t *testing.T) {
	var deleted atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/process/execute"):
			writeNodesJSON(w, map[string]any{"exitCode": 0, "result": "rdma present"})
		case r.Method == http.MethodDelete:
			deleted.Add(1)
			writeNodesJSON(w, map[string]any{})
		case strings.HasSuffix(r.URL.Path, "/exec"):
			writeNodesJSON(w, map[string]any{"cmdId": "command"})
		case r.Method == http.MethodPost:
			writeNodesJSON(w, map[string]any{"sessionId": "session", "commands": []any{}})
		default:
			writeNodesJSON(w, map[string]any{"id": "command", "command": "sleep 100"})
		}
	}))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	_, err := sandbox.Nodes.Process.ExecuteCommand(context.Background(), "sleep 100", NodeExecuteOptions{Timeout: 40 * time.Millisecond})
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.Equal(t, int32(2), deleted.Load())
}

func TestSandboxNodesStreamingCancellationClosesIdleSockets(t *testing.T) {
	var deleted, closed atomic.Int32
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if websocket.IsWebSocketUpgrade(r) {
			conn, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				return
			}
			defer conn.Close()
			defer closed.Add(1)
			_, _, _ = conn.ReadMessage()
			return
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/process/execute"):
			writeNodesJSON(w, map[string]any{"exitCode": 0, "result": ""})
		case r.Method == http.MethodDelete:
			deleted.Add(1)
			writeNodesJSON(w, map[string]any{})
		case strings.HasSuffix(r.URL.Path, "/exec"):
			writeNodesJSON(w, map[string]any{"cmdId": "command"})
		default:
			writeNodesJSON(w, map[string]any{"sessionId": "session", "commands": []any{}})
		}
	}))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err := sandbox.Nodes.Process.ExecuteCommand(ctx, "sleep 100", NodeExecuteOptions{Timeout: 60 * time.Millisecond, OnStdout: func(int32, string) { t.Error("Idle streams must not produce output") }})
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.Equal(t, int32(2), deleted.Load())
	require.Eventually(t, func() bool { return closed.Load() == 2 }, time.Second, time.Millisecond)
}

func TestSandboxNodesCleanupFailureKeepsSuccessfulResults(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/process/execute"):
			writeNodesJSON(w, map[string]any{"exitCode": 0, "result": ""})
		case r.Method == http.MethodDelete:
			w.WriteHeader(http.StatusServiceUnavailable)
		case strings.HasSuffix(r.URL.Path, "/exec"):
			writeNodesJSON(w, map[string]any{"cmdId": "command"})
		case strings.HasSuffix(r.URL.Path, "/logs"):
			writeNodesJSON(w, map[string]any{"output": "ok", "stdout": "ok", "stderr": ""})
		case r.Method == http.MethodGet:
			writeNodesJSON(w, map[string]any{"id": "command", "command": "echo ok", "exitCode": 0})
		default:
			writeNodesJSON(w, map[string]any{"sessionId": "session", "commands": []any{}})
		}
	}))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	results, err := sandbox.Nodes.Process.ExecuteCommand(context.Background(), "echo ok")
	require.NoError(t, err)
	require.Len(t, results, 2)
	for _, node := range sandbox.Nodes.All() {
		require.Equal(t, "ok", results[node.Rank].Result)
		require.NotEmpty(t, node.Warnings())
	}
}

func TestSandboxNodesUploadsReportEveryFailedRank(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests.Add(1); w.WriteHeader(http.StatusForbidden) }))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	err := sandbox.Nodes.FileSystem.UploadFile(context.Background(), []byte("test"), "/tmp/test")
	var failures *NodeErrors
	require.True(t, stderrors.As(err, &failures))
	require.Len(t, failures.Failures, 2)
	require.Equal(t, int32(2), requests.Load())
}

func TestSandboxNodesEncodePerNodeResources(t *testing.T) {
	request := apiclient.NewCreateSandbox()
	err := applyPerNodeResources(request, &types.Resources{CPUPerNode: []int{4, 8}, MemoryPerNode: []int{32, 64}, DiskPerNode: []int{100, 200}})
	require.NoError(t, err)
	encoded, err := json.Marshal(request)
	require.NoError(t, err)
	var body map[string]any
	require.NoError(t, json.Unmarshal(encoded, &body))
	require.Equal(t, []any{float64(4), float64(8)}, body["cpu"])
	require.Error(t, applyPerNodeResources(request, &types.Resources{CPU: 4, CPUPerNode: []int{4, 8}}))
	require.Error(t, applyPerNodeResources(request, &types.Resources{CPUPerNode: []int{}}))
}

func TestSandboxNodesListHydratesOnlyGangsAndSkipsOnlyNotFound(t *testing.T) {
	for _, status := range []int{http.StatusOK, http.StatusNotFound, http.StatusForbidden, http.StatusServiceUnavailable} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			var lookups atomic.Int32
			var server *httptest.Server
			server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/sandbox" {
					writeNodesJSON(w, map[string]any{"items": []apiclient.SandboxListItem{
						{Id: "cpu", Gpu: 0, ToolboxProxyUrl: server.URL + "/toolbox"},
						{Id: "head", Gpu: 16, ToolboxProxyUrl: server.URL + "/toolbox"},
					}, "nextCursor": nil})
					return
				}
				lookups.Add(1)
				require.Equal(t, "/api/sandbox/head", r.URL.Path)
				if status != http.StatusOK {
					w.WriteHeader(status)
					return
				}
				writeNodesJSON(w, nodesTestDTO(server.URL+"/toolbox"))
			}))
			defer server.Close()
			items, _, err := nodesTestClient(server).fetchPage(context.Background(), nil, nil)
			require.Equal(t, int32(1), lookups.Load())
			switch status {
			case http.StatusOK:
				require.NoError(t, err)
				require.Len(t, items, 2)
				require.Len(t, items[1].Nodes.All(), 2)
			case http.StatusNotFound:
				require.NoError(t, err)
				require.Len(t, items, 1)
				require.Equal(t, "cpu", items[0].ID)
			default:
				require.Error(t, err)
			}
		})
	}
}

func TestSandboxNodesStreamingSuccessAndAdvisoryPreflight(t *testing.T) {
	var deleted atomic.Int32
	var callbacks []int32
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if websocket.IsWebSocketUpgrade(r) {
			conn, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				return
			}
			defer conn.Close()
			_ = conn.WriteMessage(websocket.BinaryMessage, append([]byte{1, 1, 1}, []byte("ok")...))
			_ = conn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
			return
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/process/execute"):
			writeNodesJSON(w, map[string]any{"exitCode": 1, "result": "missing rdma"})
		case r.Method == http.MethodDelete:
			deleted.Add(1)
			writeNodesJSON(w, map[string]any{})
		case strings.HasSuffix(r.URL.Path, "/exec"):
			writeNodesJSON(w, map[string]any{"cmdId": "command"})
		case strings.HasSuffix(r.URL.Path, "/logs"):
			writeNodesJSON(w, map[string]any{"output": "ok", "stdout": "ok", "stderr": ""})
		case r.Method == http.MethodGet:
			writeNodesJSON(w, map[string]any{"id": "command", "command": "echo ok", "exitCode": 0})
		default:
			writeNodesJSON(w, map[string]any{"sessionId": "session", "commands": []any{}})
		}
	}))
	defer server.Close()
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL+"/toolbox"))
	result, err := sandbox.Nodes.Process.ExecuteCommand(context.Background(), "echo ok", NodeExecuteOptions{
		Timeout:  time.Second,
		OnStdout: func(rank int32, chunk string) { require.Equal(t, "ok", chunk); callbacks = append(callbacks, rank) },
	})
	require.NoError(t, err)
	require.ElementsMatch(t, []int32{0, 1}, callbacks)
	require.Equal(t, int32(2), deleted.Load())
	for _, node := range sandbox.Nodes.All() {
		require.Contains(t, result[node.Rank].Result, "WARNING:")
		require.Contains(t, result[node.Rank].Result, "ok")
		require.NotEmpty(t, node.Warnings())
	}
}

func TestSandboxNodesPreviewAndSSHRouteToMember(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Contains(t, r.URL.Path, "/api/sandbox/worker/")
		require.Equal(t, "Bearer test-api-key", r.Header.Get("Authorization"))
		if strings.HasSuffix(r.URL.Path, "/preview-url") {
			writeNodesJSON(w, apiclient.PortPreviewUrl{SandboxId: "worker", Url: "https://preview.example.com", Token: "token"})
		} else if r.Method == http.MethodDelete {
			writeNodesJSON(w, nodesTestDTO("https://proxy.example.com"))
		} else {
			writeNodesJSON(w, apiclient.SshAccessDto{Id: "access", SandboxId: "worker", Token: "token", SshCommand: "ssh example"})
		}
	}))
	defer server.Close()
	worker, err := nodesTestSandbox(t, server, nodesTestDTO(server.URL)).Nodes.Get(1)
	require.NoError(t, err)
	_, err = worker.GetPreviewLink(context.Background(), 8080)
	require.NoError(t, err)
	_, err = worker.CreateSSHAccess(context.Background(), nil)
	require.NoError(t, err)
	require.NoError(t, worker.RevokeSSHAccess(context.Background(), "token"))
}

func TestSandboxNodesUploadsLocalFileToEveryMember(t *testing.T) {
	var uploaded atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		file, _, err := r.FormFile("file")
		require.NoError(t, err)
		defer file.Close()
		content, err := io.ReadAll(file)
		require.NoError(t, err)
		require.Equal(t, "shared contents", string(content))
		require.Equal(t, "/tmp/shared.txt", r.URL.Query().Get("path"))
		uploaded.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	path := filepath.Join(t.TempDir(), "upload.txt")
	require.NoError(t, os.WriteFile(path, []byte("shared contents"), 0o600))
	sandbox := nodesTestSandbox(t, server, nodesTestDTO(server.URL))
	require.NoError(t, sandbox.Nodes.FileSystem.UploadFile(context.Background(), path, "/tmp/shared.txt"))
	require.Equal(t, int32(2), uploaded.Load())
}
