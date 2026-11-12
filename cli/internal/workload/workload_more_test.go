// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	api "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParseTimeout_negativeRejected(t *testing.T) {
	_, err := ParseTimeout("-1s")
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParseTimeout_zeroRejected(t *testing.T) {
	_, err := ParseTimeout("0s")
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParseTimeout_valid(t *testing.T) {
	d, err := ParseTimeout("5s")
	require.NoError(t, err)
	require.Equal(t, 5*time.Second, d)
}

func TestLongPollMillis_shortTimeout(t *testing.T) {
	require.Equal(t, int64(500), longPollMillis(500*time.Millisecond))
}

func TestLongPollMillis_normalTimeout(t *testing.T) {
	require.Equal(t, int64(10000), longPollMillis(10*time.Second))
}

func TestLongPollMillis_longTimeout(t *testing.T) {
	require.Equal(t, int64(55000), longPollMillis(5*time.Minute))
}

func TestWaitForSandbox_terminalFailure(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(sandboxFixture("failed")))
	}))
	defer srv.Close()
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 1 * time.Second})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestWaitForSandbox_deletedTerminal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(sandboxFixture("deleted")))
	}))
	defer srv.Close()
	// Waiting for "running" but sandbox is "deleted" → conflict
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 1 * time.Second})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestWaitForSandbox_lostTerminal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(sandboxFixture("lost")))
	}))
	defer srv.Close()
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 1 * time.Second})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestWaitForSandbox_stoppedUsesWaitExact(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "stopped", r.URL.Query().Get("wait_exact"))
		_, _ = w.Write([]byte(sandboxFixture("stopped")))
	}))
	defer srv.Close()
	detail, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "stopped", Timeout: 1 * time.Second})
	require.NoError(t, err)
	require.Equal(t, "stopped", detail.V.Status)
}

func TestWaitForSandbox_deletedUsesWaitExact(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "deleted", r.URL.Query().Get("wait_exact"))
		_, _ = w.Write([]byte(sandboxFixture("deleted")))
	}))
	defer srv.Close()
	detail, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "deleted", Timeout: 1 * time.Second})
	require.NoError(t, err)
	require.Equal(t, "deleted", detail.V.Status)
}

func TestWaitForSandbox_refusesANonStatusTargetBeforeAnyRequest(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected request %s", r.URL)
	}))
	defer srv.Close()
	for _, target := range []string{"", "started", "Running", "ready", "destroyed"} {
		_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: target, Timeout: time.Second})
		require.Error(t, err, target)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), target)
	}
}

func TestWaitForImage_failedTerminal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"region_ids":["region-a"],"created_at":"2026-09-13T00:00:00Z","id":"11111111-1111-1111-1111-111111111111","name":"img","region_id":"region-a","source":{"type":"registry","ref":"python:3.12"},"status":"failed"}`))
	}))
	defer srv.Close()
	_, err := WaitForImage(context.Background(), api.New(srv.URL, "k", "test"), "img", 1*time.Second)
	require.Error(t, err)
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
}

func TestWaitForSnapshot_failedTerminal(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"region_ids":["region-a"],"id":"11111111-1111-1111-1111-111111111111","kind":"full","name":"snap","region_id":"region-a","status":"failed"}`))
	}))
	defer srv.Close()
	_, err := WaitForSnapshot(context.Background(), api.New(srv.URL, "k", "test"), "snap", 1*time.Second)
	require.Error(t, err)
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
}

func TestWaitForImage_escapesIDPathSegment(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/images/img#1", r.URL.Path)
		require.Equal(t, "ready", r.URL.Query().Get("wait"))
		require.Equal(t, "1000", r.URL.Query().Get("timeout_ms"))
		_, _ = w.Write([]byte(`{"region_ids":["region-a"],"created_at":"2026-09-13T00:00:00Z","id":"11111111-1111-1111-1111-111111111111","name":"img","region_id":"region-a","source":{"type":"registry","ref":"python:3.12"},"status":"ready"}`))
	}))
	defer srv.Close()
	_, err := WaitForImage(context.Background(), api.New(srv.URL, "k", "test"), "img#1", 1*time.Second)
	require.NoError(t, err)
}

func TestWaitForSnapshot_escapesIDPathSegment(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/snapshots/snap#1", r.URL.Path)
		require.Equal(t, "ready", r.URL.Query().Get("wait"))
		require.Equal(t, "1000", r.URL.Query().Get("timeout_ms"))
		_, _ = w.Write([]byte(`{"region_ids":["region-a"],"id":"11111111-1111-1111-1111-111111111111","kind":"full","name":"snap","region_id":"region-a","status":"ready"}`))
	}))
	defer srv.Close()
	_, err := WaitForSnapshot(context.Background(), api.New(srv.URL, "k", "test"), "snap#1", 1*time.Second)
	require.NoError(t, err)
}

func TestWaitForSandbox_contextTimeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(sandboxFixture("pending")))
	}))
	defer srv.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	_, err := WaitForSandbox(ctx, api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 200 * time.Millisecond})
	require.Error(t, err)
}

func TestNestedBody_existingMapStringAny(t *testing.T) {
	body := input.Body{
		"lifecycle_policy": map[string]any{"idle_expiry_seconds": float64(600)},
	}
	nested := NestedBody(body, "lifecycle_policy")
	nested.Set("timeout_seconds", float64(3600))
	// The original map should be converted to input.Body
	result, ok := body["lifecycle_policy"].(input.Body)
	require.True(t, ok)
	require.Equal(t, float64(3600), result["timeout_seconds"])
	require.Equal(t, float64(600), result["idle_expiry_seconds"])
}

func TestNestedBody_existingInputBody(t *testing.T) {
	inner := input.Body{"a": 1}
	body := input.Body{"nested": inner}
	nested := NestedBody(body, "nested")
	nested.Set("b", 2)
	require.Equal(t, 1, inner["a"])
	require.Equal(t, 2, inner["b"])
}

func TestNestedBody_newKey(t *testing.T) {
	body := input.Body{}
	nested := NestedBody(body, "new")
	nested.Set("x", "y")
	result, ok := body["new"].(input.Body)
	require.True(t, ok)
	require.Equal(t, "y", result["x"])
}

func TestParsePorts_withProto(t *testing.T) {
	ports, err := ParsePorts([]string{"443/tcp", "80"})
	require.NoError(t, err)
	require.Len(t, ports, 2)
	require.Equal(t, int32(443), ports[0].Port)
	require.NotNil(t, ports[0].Proto)
	require.Equal(t, "tcp", *ports[0].Proto)
	require.Equal(t, int32(80), ports[1].Port)
	require.Nil(t, ports[1].Proto)
}

func TestParsePorts_invalidPort(t *testing.T) {
	_, err := ParsePorts([]string{"99999"})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParsePorts_invalidProto(t *testing.T) {
	_, err := ParsePorts([]string{"443/https"})
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestParsePorts_emptyProtoAfterSlash(t *testing.T) {
	for _, value := range []string{"8080/", "8080/ "} {
		_, err := ParsePorts([]string{value})
		require.Error(t, err, value)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), value)
	}
}

func sandboxFixture(status string) string {
	return `{"class":"linux","spot":false,"cpu":1,"id":"id","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-a","disk_mib":1024,"status":"` + status + `","toolbox_proxy_url":"https://tb.example","cores_max":1}`
}

// A sequence of statuses served one per poll.
func sandboxSequence(t *testing.T, statuses ...string) *httptest.Server {
	var i int32
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		n := atomic.AddInt32(&i, 1) - 1
		if int(n) >= len(statuses) {
			n = int32(len(statuses) - 1)
		}
		_, _ = w.Write([]byte(sandboxFixture(statuses[n])))
	}))
}

// `start` on a stopped sandbox: the first `?wait=running` long-poll answers
// with the still-stopped status before the transition becomes visible.
// That is the state we are leaving, not a conflict.
func TestWaitForSandbox_startFromStoppedSucceeds(t *testing.T) {
	srv := sandboxSequence(t, "stopped", "booting", "running")
	defer srv.Close()
	d, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 5 * time.Second, AllowInitialStopped: true})
	require.NoError(t, err)
	require.Equal(t, "running", d.V.Status)
}

// But a sandbox that was under way and then DROPS into stopping/stopped has
// lost the race — that is a conflict, not something to wait out.
func TestWaitForSandbox_fallingBackToStoppedConflicts(t *testing.T) {
	for _, status := range []string{"stopping", "stopped"} {
		t.Run(status, func(t *testing.T) {
			srv := sandboxSequence(t, "booting", status, status)
			defer srv.Close()
			_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 5 * time.Second})
			require.Error(t, err)
			require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
		})
	}
}

func TestWaitForSandbox_forkWaitsPastStopped(t *testing.T) {
	for _, statuses := range [][]string{
		{"stopped", "booting", "running"},
		{"pending", "stopping", "stopped", "booting", "running"},
	} {
		t.Run(strings.Join(statuses, "-"), func(t *testing.T) {
			srv := sandboxSequence(t, statuses...)
			defer srv.Close()
			detail, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 5 * time.Second, StoppedPassed: true})
			require.NoError(t, err)
			require.Equal(t, "running", detail.V.Status)
		})
	}
}

func TestWaitForSandbox_deletingConflictsImmediately(t *testing.T) {
	srv := sandboxSequence(t, "deleting")
	defer srv.Close()
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: time.Second})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
}

func TestWaitForSandbox_deletingProgressesToDeleted(t *testing.T) {
	srv := sandboxSequence(t, "deleting", "deleted")
	defer srv.Close()
	detail, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "deleted", Timeout: 5 * time.Second})
	require.NoError(t, err)
	require.Equal(t, "deleted", detail.V.Status)
}

// A delete the server could not finish reads failed with an explanation; the
// conflict carries it, so the user learns why without a second command.
func TestWaitForSandbox_failedDeleteNamesTheReason(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if atomic.AddInt32(&calls, 1) == 1 {
			_, _ = w.Write([]byte(sandboxFixture("deleting")))
			return
		}
		failed := strings.Replace(sandboxFixture("failed"), `"status":"failed"`, `"status":"failed","error":"sandbox deletion did not finish"`, 1)
		_, _ = w.Write([]byte(failed))
	}))
	defer srv.Close()
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "deleted", Timeout: 5 * time.Second})
	require.Error(t, err)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	require.Equal(t, "sandbox id reached failed, not deleted: sandbox deletion did not finish", err.Error())
}

func TestWaitForSandbox_failedWithoutReasonKeepsTheShortMessage(t *testing.T) {
	srv := sandboxSequence(t, "failed")
	defer srv.Close()
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "deleted", Timeout: time.Second})
	require.Error(t, err)
	require.Equal(t, "sandbox id reached failed, not deleted", err.Error())
}

func TestWaitForSandbox_capsLongPollToRemainingDeadline(t *testing.T) {
	var timeoutMs string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		timeoutMs = r.URL.Query().Get("timeout_ms")
		_, _ = w.Write([]byte(sandboxFixture("running")))
	}))
	defer srv.Close()
	_, err := WaitForSandbox(context.Background(), api.New(srv.URL, "k", "test"), SandboxWaitOptions{ID: "id", Target: "running", Timeout: 500 * time.Millisecond})
	require.NoError(t, err)
	require.Equal(t, "500", timeoutMs)
}
