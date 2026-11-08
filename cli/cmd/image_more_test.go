// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	apiinternal "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestImageList_fansOutAcrossRegionsAndMergesCursors(t *testing.T) {
	homeURL, cleanup := startImageListRegionServers(t, map[string]int{"region-a": 1, "region-b": 1})
	defer cleanup()

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", homeURL, "--api-key", "k", "-o", "json", "image", "list", "--limit", "2"})
	require.NoError(t, root.ExecuteContext(context.Background()))

	out := requireJSONStdout(t, stdout.String())
	items := out["items"].([]any)
	require.Len(t, items, 2)
	require.Equal(t, "region-a-first", items[0].(map[string]any)["name"])
	require.Equal(t, "region-b-first", items[1].(map[string]any)["name"])
	require.Equal(t, "region-a:c-region-a-1,region-b:c-region-b-1", out["next_cursor"])
}

func TestImageList_allTraversesRegionalPagesExactlyOnce(t *testing.T) {
	homeURL, cleanup := startImageListRegionServers(t, map[string]int{"region-a": 2, "region-b": 2})
	defer cleanup()

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", homeURL, "--api-key", "k", "-o", "json", "image", "list", "--limit", "2", "--all"})
	require.NoError(t, root.ExecuteContext(context.Background()))

	out := requireJSONStdout(t, stdout.String())
	items := out["items"].([]any)
	require.Len(t, items, 4)
	names := []string{}
	ids := map[string]struct{}{}
	for _, item := range items {
		resource := item.(map[string]any)
		names = append(names, resource["name"].(string))
		ids[resource["id"].(string)] = struct{}{}
	}
	require.Equal(t, []string{"region-a-first", "region-b-first", "region-a-second", "region-b-second"}, names)
	require.Len(t, ids, 4)
	_, hasNext := out["next_cursor"]
	require.False(t, hasNext)
}

// The image list shares the sandbox list's fan-out trimming, so it shares the
// hazard: one API serves every listed region that publishes no URL of its own,
// and an item trimmed off a merged page must return through its request's cursor,
// not through a key named after the item's own region.
func TestImageListAll_oneAPIServingSeveralRegionsLosesNoTrimmedItem(t *testing.T) {
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/images", r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Query().Get("cursor") {
		case "":
			_, _ = io.WriteString(w, imageRegionPageFixture(`{"id":"33333333-3333-3333-3333-333333333333","name":"remote-one","region_id":"region-0","created_at":"2026-09-14T00:00:04Z","cursor":"r1"}`, "r1"))
		case "r1":
			_, _ = io.WriteString(w, imageRegionPageFixture(`{"id":"44444444-4444-4444-4444-444444444444","name":"remote-two","region_id":"region-0","created_at":"2026-09-14T00:00:02Z","cursor":"r2"}`, ""))
		default:
			assert.Fail(t, "unexpected remote cursor "+r.URL.Query().Get("cursor"))
		}
	}))
	defer remote.Close()

	regionB := imageRegionItemFixture(`{"id":"22222222-2222-2222-2222-222222222222","name":"home-two","region_id":"region-b","created_at":"2026-09-14T00:00:03Z","cursor":"h2"}`)
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/regions":
			_, _ = io.WriteString(w, `{"regions":[
			{"id":"earth","name":"Earth","available":true,"parent_region_ids":[]},
			{"id":"region-0","name":"Region 0","available":true,"parent_region_ids":["earth"],"api_base_url":"`+remote.URL+`"},
			{"id":"region-a","name":"Region A","available":true,"parent_region_ids":["earth"]},
			{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"]}
		]}`)
		case "/images":
			switch r.URL.Query().Get("cursor") {
			case "":
				_, _ = io.WriteString(w, `{"images":[`+imageRegionItemFixture(`{"id":"11111111-1111-1111-1111-111111111111","name":"home-one","region_id":"region-a","created_at":"2026-09-14T00:00:05Z","cursor":"h1"}`)+`,`+regionB+`]}`)
			case "h1":
				_, _ = io.WriteString(w, `{"images":[`+regionB+`]}`)
			default:
				assert.Fail(t, "unexpected home cursor "+r.URL.Query().Get("cursor"))
			}
		default:
			assert.Fail(t, "unexpected home path "+r.URL.Path)
		}
	}))
	defer home.Close()

	stdout := &bytes.Buffer{}
	root := NewRoot(stdout, &bytes.Buffer{})
	root.SetArgs([]string{"--api-url", home.URL, "--api-key", "k", "-o", "json", "image", "list", "--limit", "2", "--all"})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	require.NoError(t, root.ExecuteContext(ctx))

	names := []string{}
	for _, item := range requireJSONStdout(t, stdout.String())["items"].([]any) {
		names = append(names, item.(map[string]any)["name"].(string))
	}
	require.Equal(t, []string{"home-one", "remote-one", "home-two", "remote-two"}, names,
		"every item once: the trimmed region-b item returns through its request's cursor")
}

func TestImageImport_missingRef(t *testing.T) {
	_, _, err := runCLI(t, []string{"image", "create", "img"}, nil)
	// RunE checks the merged body: without --ref (or a source in --file) it
	// is a usage error before any request.
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "--ref is required")
}

func TestImageImport_rejectsNullSourceFromFile(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "image.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"source":null}`), 0o600))
	_, _, err := runCLI(t, []string{"image", "create", "img", "--file", bodyPath}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "--ref is required")
}

func TestImageLogs_followQuery(t *testing.T) {
	stdout, _, err := runCLIWithResponseReplacement(t, []string{
		"image", "logs", "11111111-1111-1111-1111-111111111111", "--follow",
	}, []expectedRequest{
		{method: http.MethodGet, path: "/images/11111111-1111-1111-1111-111111111111/build-logs-url", status: 200, response: `{"url":"__SERVER__/build-logs/images/11111111-1111-1111-1111-111111111111"}`},
		{method: http.MethodGet, path: "/build-logs/images/11111111-1111-1111-1111-111111111111", query: "follow=true&format=text", status: 200, response: "text/plain:building...\n"},
	})
	require.NoError(t, err)
	require.Contains(t, stdout, "building...")
}

func TestImageLogs_followStreamsSanitizedChunksBeforeInterrupt(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	streaming := make(chan struct{})
	secondChunk := make(chan struct{})
	serverURL := ""
	// Handler assertions are non-fatal: FailNow may only run on the test goroutine.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		assert.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
		switch r.URL.Path {
		case "/images/11111111-1111-1111-1111-111111111111/build-logs-url":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"url":"`+serverURL+`/build-logs/images/11111111-1111-1111-1111-111111111111"}`)
		case "/build-logs/images/11111111-1111-1111-1111-111111111111":
			assert.Equal(t, "follow=true&format=text", r.URL.RawQuery)
			w.Header().Set("Content-Type", "text/plain")
			flusher, ok := w.(http.Flusher)
			if !assert.True(t, ok) {
				return
			}
			_, _ = io.WriteString(w, "first\n\x1b[")
			flusher.Flush()
			close(streaming)
			_, _ = io.WriteString(w, "31mred\tok\n")
			flusher.Flush()
			close(secondChunk)
			<-r.Context().Done()
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer srv.Close()
	serverURL = srv.URL
	go func() {
		<-streaming
		<-secondChunk
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "image", "logs", "11111111-1111-1111-1111-111111111111", "--follow", "-o", "table"})
	err := root.ExecuteContext(ctx)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.Equal(t, "first\nred\tok\n", stdout.String())
}

func TestImageLogs_followJSONStreamsNDJSONChunks(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	streaming := make(chan struct{})
	secondChunk := make(chan struct{})
	serverURL := ""
	// Handler assertions are non-fatal: FailNow may only run on the test goroutine.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		assert.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
		switch r.URL.Path {
		case "/images/11111111-1111-1111-1111-111111111111/build-logs-url":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"url":"`+serverURL+`/build-logs/images/11111111-1111-1111-1111-111111111111"}`)
		case "/build-logs/images/11111111-1111-1111-1111-111111111111":
			assert.Equal(t, "follow=true&format=text", r.URL.RawQuery)
			w.Header().Set("Content-Type", "text/plain")
			flusher, ok := w.(http.Flusher)
			if !assert.True(t, ok) {
				return
			}
			_, _ = io.WriteString(w, "first\n\x1b[")
			flusher.Flush()
			close(streaming)
			_, _ = io.WriteString(w, "31mred\tok\n")
			flusher.Flush()
			close(secondChunk)
			<-r.Context().Done()
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer srv.Close()
	serverURL = srv.URL
	go func() {
		<-streaming
		<-secondChunk
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "image", "logs", "11111111-1111-1111-1111-111111111111", "--follow", "-o", "json"})
	err := root.ExecuteContext(ctx)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	// JSON data is not a terminal: the bytes arrive unsanitized, one
	// {"data": ...} object per chunk.
	var joined strings.Builder
	for _, line := range strings.Split(strings.TrimSpace(stdout.String()), "\n") {
		var chunk map[string]string
		require.NoError(t, json.Unmarshal([]byte(line), &chunk), line)
		require.Len(t, chunk, 1)
		joined.WriteString(chunk["data"])
	}
	require.Equal(t, "first\n\x1b[31mred\tok\n", joined.String())
}

func TestImageLogs_rejectsInvalidDiscoveryURLBeforeForwardingCredential(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"image", "logs", "11111111-1111-1111-1111-111111111111",
	}, []expectedRequest{
		{method: http.MethodGet, path: "/images/11111111-1111-1111-1111-111111111111/build-logs-url", status: 200, response: `{"url":"https://alice:secret@logs.example/build-logs/images/11111111-1111-1111-1111-111111111111?token=secret"}`},
	})
	require.Error(t, err)
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
	require.ErrorContains(t, err, `invalid build log URL "https://logs.example/build-logs/images/11111111-1111-1111-1111-111111111111"`)
}

func TestImageBuild_directoryContextArchivesTar(t *testing.T) {
	root := t.TempDir()
	contextDir := filepath.Join(root, "context")
	require.NoError(t, os.MkdirAll(contextDir, 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(contextDir, "demo.txt"), []byte("payload"), 0o600))
	stdout, _, err := runCLI(t,
		[]string{"image", "build", "img", "--dockerfile", "Dockerfile", "--context", contextDir, "--no-wait"},
		[]expectedRequest{{
			method:      http.MethodPost,
			path:        "/images",
			contentType: "multipart/form-data",
			status:      202,
			response:    `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
			check: func(t *testing.T, r *http.Request, body []byte) {
				parts := readMultipartParts(t, r.Header.Get("Content-Type"), body)
				require.Equal(t, "img", parts["name"])
				require.Equal(t, "Dockerfile", parts["dockerfile"])
				tr := tar.NewReader(bytes.NewReader([]byte(parts["context"])))
				header, nextErr := tr.Next()
				require.NoError(t, nextErr)
				require.Equal(t, "demo.txt", header.Name)
				content, readErr := io.ReadAll(tr)
				require.NoError(t, readErr)
				require.Equal(t, "payload", string(content))
			},
		}},
	)
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestImageBuild_rejectsNonRegularContextEntries(t *testing.T) {
	if testing.Short() {
		t.Skip("requires unix socket entry")
	}
	// A short directory: a socket path is limited to about 104 bytes, which a
	// long TMPDIR plus t.TempDir()'s name can exceed.
	dir := ""
	if len(os.TempDir()) > 64 {
		dir = "/tmp"
	}
	root, err := os.MkdirTemp(dir, "s")
	require.NoError(t, err)
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	contextDir := filepath.Join(root, "c")
	require.NoError(t, os.MkdirAll(contextDir, 0o755))
	socketPath := filepath.Join(contextDir, "b.sock")
	listener, err := net.Listen("unix", socketPath)
	require.NoError(t, err)
	defer listener.Close()
	f := newFakeAPI(t)
	d := cfgDir(t)
	_, errb, code := run(t, d, nil,
		"--api-url", f.srv.URL,
		"--api-key", "k",
		"--project", testProjP1,
		"image", "build", "img", "--dockerfile", "Dockerfile", "--context", contextDir, "--no-wait",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, errb, socketPath)
	require.Empty(t, f.requests)
}

func TestImageBuild_rejectsOversizedContextAsUsage(t *testing.T) {
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, nil, 0o600))
	require.NoError(t, os.Truncate(contextPath, imageContextMaxBytes+1))
	f := newFakeAPI(t)
	stdout, stderr, code := run(t, cfgDir(t), nil,
		"--api-url", f.srv.URL,
		"--api-key", "k",
		"--project", testProjP1,
		"image", "build", "img", "--dockerfile", "Dockerfile", "--context", contextPath, "--no-wait",
	)
	require.Equal(t, cerr.ExitUsage, code)
	require.Empty(t, stdout)
	require.Contains(t, stderr, "64 MiB")
	require.Empty(t, f.requests)
}

func TestSanitizeBuildLogURL_truncatesLongMalformedURL(t *testing.T) {
	raw := strings.Repeat("a", 260)
	sanitized := sanitizeBuildLogURL(raw)
	require.LessOrEqual(t, len(sanitized), 203)
	require.True(t, strings.HasSuffix(sanitized, "…"))
}

func TestValidatedBuildLogBaseURL_rejectsTrailingRegionDash(t *testing.T) {
	_, err := validatedBuildLogBaseURL(
		"/sandboxes/7fJk2mQpXv9dLc3RtY-region-a/build-logs-url",
		"https://logs.example/build-logs/sandboxes/7fJk2mQpXv9dLc3RtY-",
	)
	require.Error(t, err)
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
}

func TestImageBuild_fileCpuArchBecomesMultipartMeta(t *testing.T) {
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, []byte("tar-bytes"), 0o600))
	bodyPath := filepath.Join(root, "body.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"dockerfile":"Dockerfile","context":"`+contextPath+`","cpu_arch":"arm64"}`), 0o600))
	stdout, _, err := runCLI(t,
		[]string{"image", "build", "img", "--file", bodyPath, "--no-wait"},
		[]expectedRequest{{
			method:      http.MethodPost,
			path:        "/images",
			contentType: "multipart/form-data",
			status:      202,
			response:    `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
			check: func(t *testing.T, r *http.Request, body []byte) {
				parts := readMultipartParts(t, r.Header.Get("Content-Type"), body)
				require.Equal(t, "arm64", parts["cpu_arch"])
			},
		}},
	)
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestImageBuild_regionFlagOverridesFileRegion(t *testing.T) {
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, []byte("tar-bytes"), 0o600))
	bodyPath := filepath.Join(root, "body.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"dockerfile":"Dockerfile","context":"`+contextPath+`","region_id":"region-b"}`), 0o600))
	stdout, _, err := runCLI(t,
		[]string{"--region", "region-a", "image", "build", "img", "--file", bodyPath, "--no-wait"},
		[]expectedRequest{{
			method:      http.MethodPost,
			path:        "/images",
			contentType: "multipart/form-data",
			status:      202,
			response:    `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
			check: func(t *testing.T, r *http.Request, body []byte) {
				parts := readMultipartParts(t, r.Header.Get("Content-Type"), body)
				require.Equal(t, "region-a", parts["region_id"])
			},
		}},
	)
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestPostImageMultipart_retriesUnauthorizedWhenBodyIsSeekable(t *testing.T) {
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, []byte("tar-bytes"), 0o600))
	var calls atomic.Int32
	var firstBody []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		require.NoError(t, err)
		call := calls.Add(1)
		if call == 1 {
			firstBody = append([]byte(nil), body...)
			require.Equal(t, "Bearer old", r.Header.Get("Authorization"))
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = io.WriteString(w, `{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`)
			return
		}
		require.Equal(t, int32(2), call)
		require.Equal(t, "Bearer new", r.Header.Get("Authorization"))
		require.Equal(t, firstBody, body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`)
	}))
	defer srv.Close()
	client := apiinternal.New(srv.URL, "old", "test")
	client.OnUnauthorized = func(context.Context) (string, error) { return "new", nil }
	resp, err := postImageMultipart(context.Background(), client, map[string]string{"name": "img"}, "Dockerfile", contextPath)
	require.NoError(t, err)
	require.Equal(t, "11111111-1111-1111-1111-111111111111", resp.V.Id)
	require.Equal(t, int32(2), calls.Load())
}

func TestPostImageMultipart_largeContextIsOneShot(t *testing.T) {
	oldLimit := imageContextReplayLimit
	imageContextReplayLimit = 8
	defer func() { imageContextReplayLimit = oldLimit }()
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, []byte("large-context"), 0o600))
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		_ = r.Body.Close()
		call := calls.Add(1)
		if call == 1 {
			require.Equal(t, "Bearer old", r.Header.Get("Authorization"))
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = io.WriteString(w, `{"code":"UNAUTHORIZED","message":"unauthorized","source":"DAYTONA_API"}`)
			return
		}
		t.Fatalf("unexpected retry %d", call)
	}))
	defer srv.Close()
	client := apiinternal.New(srv.URL, "old", "test")
	client.OnUnauthorized = func(context.Context) (string, error) { return "new", nil }
	_, err := postImageMultipart(context.Background(), client, map[string]string{"name": "img"}, "Dockerfile", contextPath)
	require.Error(t, err)
	require.Equal(t, cerr.ExitAuth, cerr.ExitCode(err))
	require.Equal(t, int32(1), calls.Load())
}

func TestPostStreamedImageMultipart_closesProducerWhenRequestBuildFails(t *testing.T) {
	readerClosed := make(chan struct{})
	client := apiinternal.New("://bad-url", "old", "test")
	_, err := postStreamedImageMultipart(context.Background(), client, map[string]string{"name": "img"}, "Dockerfile", imageContextUpload{
		name: "context.tar",
		size: imageContextReplayLimit + 1,
		open: func() (io.ReadCloser, error) {
			return &closingReadCloser{Reader: strings.NewReader(strings.Repeat("x", 1024)), closed: readerClosed}, nil
		},
	})
	require.Error(t, err)
	select {
	case <-readerClosed:
	case <-time.After(time.Second):
		t.Fatal("multipart producer goroutine did not exit after request construction failed")
	}
}

func TestImageBuild_defaultsDockerfileWhenOmitted(t *testing.T) {
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, []byte("tar-bytes"), 0o600))
	stdout, _, err := runCLI(t,
		[]string{"image", "build", "img", "--context", contextPath, "--no-wait"},
		[]expectedRequest{{
			method:      http.MethodPost,
			path:        "/images",
			contentType: "multipart/form-data",
			status:      202,
			response:    `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
			check: func(t *testing.T, r *http.Request, body []byte) {
				parts := readMultipartParts(t, r.Header.Get("Content-Type"), body)
				require.Equal(t, "Dockerfile", parts["dockerfile"])
			},
		}},
	)
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestImageBuild_symlinkedDirectoryContextArchivesTargetContents(t *testing.T) {
	root := t.TempDir()
	actualDir := filepath.Join(root, "actual")
	require.NoError(t, os.MkdirAll(actualDir, 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(actualDir, "demo.txt"), []byte("payload"), 0o600))
	contextPath := filepath.Join(root, "context-link")
	require.NoError(t, os.Symlink(actualDir, contextPath))
	stdout, _, err := runCLI(t,
		[]string{"image", "build", "img", "--dockerfile", "Dockerfile", "--context", contextPath, "--no-wait"},
		[]expectedRequest{{
			method:      http.MethodPost,
			path:        "/images",
			contentType: "multipart/form-data",
			status:      202,
			response:    `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
			check: func(t *testing.T, r *http.Request, body []byte) {
				parts := readMultipartParts(t, r.Header.Get("Content-Type"), body)
				tr := tar.NewReader(bytes.NewReader([]byte(parts["context"])))
				header, nextErr := tr.Next()
				require.NoError(t, nextErr)
				require.Equal(t, "demo.txt", header.Name)
				content, readErr := io.ReadAll(tr)
				require.NoError(t, readErr)
				require.Equal(t, "payload", string(content))
			},
		}},
	)
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestImageDelete_requiresYes(t *testing.T) {
	_, _, err := runCLI(t, []string{"image", "delete", "11111111-1111-1111-1111-111111111111"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func readMultipartParts(t *testing.T, contentType string, body []byte) map[string]string {
	t.Helper()
	_, params, err := mime.ParseMediaType(contentType)
	require.NoError(t, err)
	reader := multipart.NewReader(bytes.NewReader(body), params["boundary"])
	parts := map[string]string{}
	for {
		part, partErr := reader.NextPart()
		if partErr == io.EOF {
			return parts
		}
		require.NoError(t, partErr)
		data, readErr := io.ReadAll(part)
		require.NoError(t, readErr)
		parts[part.FormName()] = string(data)
	}
}

func startImageListRegionServers(t *testing.T, expectedCounts map[string]int) (string, func()) {
	t.Helper()
	var regionRequestsMu sync.Mutex
	regionRequests := map[string]int{}
	regionServer := func(region string, pages map[string]string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			assert.Equal(t, http.MethodGet, r.Method)
			assert.Equal(t, "/images", r.URL.Path)
			assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
			assert.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
			assert.Equal(t, "2", r.URL.Query().Get("limit"))
			cursor := r.URL.Query().Get("cursor")
			key := cursor
			if key == "" {
				key = "first"
			}
			regionRequestsMu.Lock()
			regionRequests[region]++
			regionRequestsMu.Unlock()
			response, ok := pages[key]
			assert.True(t, ok, "unexpected cursor %q for region %s", cursor, region)
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, response)
		}))
	}

	regionA := regionServer("region-a", map[string]string{
		"first":        imageRegionPageFixture(`{"id":"11111111-1111-1111-1111-111111111111","name":"region-a-first","region_id":"region-a","created_at":"2026-09-14T00:00:00Z","cursor":"c-region-a-1"}`, "n-region-a-1"),
		"c-region-a-1": imageRegionPageFixture(`{"id":"33333333-3333-3333-3333-333333333333","name":"region-a-second","region_id":"region-a","created_at":"2026-09-12T00:00:00Z","cursor":"c-region-a-2"}`, ""),
	})
	regionB := regionServer("region-b", map[string]string{
		"first":        imageRegionPageFixture(`{"id":"22222222-2222-2222-2222-222222222222","name":"region-b-first","region_id":"region-b","created_at":"2026-09-13T00:00:00Z","cursor":"c-region-b-1"}`, "n-region-b-1"),
		"c-region-b-1": imageRegionPageFixture(`{"id":"44444444-4444-4444-4444-444444444444","name":"region-b-second","region_id":"region-b","created_at":"2026-09-11T00:00:00Z","cursor":"c-region-b-2"}`, ""),
	})
	// Every region publishes a URL, so only those two URLs are queried.
	home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodGet, r.Method)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/regions":
			_, _ = io.WriteString(w, `{"regions":[
			{"id":"region-a","name":"Region A","available":true,"parent_region_ids":["earth"],"api_base_url":"`+regionA.URL+`"},
			{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"],"api_base_url":"`+regionB.URL+`"}
		],"default_region_id":null}`)
		case "/images":
			regionRequestsMu.Lock()
			regionRequests["home"]++
			regionRequestsMu.Unlock()
			_, _ = io.WriteString(w, `{"images":[]}`)
		default:
			assert.Fail(t, "unexpected home path "+r.URL.Path)
		}
	}))

	cleanup := func() {
		home.Close()
		regionA.Close()
		regionB.Close()
		regionRequestsMu.Lock()
		defer regionRequestsMu.Unlock()
		assert.Equal(t, expectedCounts, regionRequests)
	}
	return home.URL, cleanup
}

func imageRegionPageFixture(item string, nextCursor string) string {
	page := `{"images":[` + imageRegionItemFixture(item) + `]`
	if nextCursor == "" {
		return page + `}`
	}
	return page + `,"next_cursor":"` + nextCursor + `"}`
}

func imageRegionItemFixture(core string) string {
	return strings.TrimSuffix(core, `}`) + `,"anonymous":false,"region_ids":["region-b"],"class":"linux","format_outdated":false,"format_version":2,"platform":false,"default":false,"deprecated":false,"origin":"registry","retention":"permanent","source":{"type":"registry","ref":"python:3.12"},"status":"ready"}`
}

func runCLIText(t *testing.T, args []string, expected []expectedRequest) (string, string, error) {
	t.Helper()
	var index int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Less(t, index, len(expected), "unexpected request %s %s", r.Method, r.URL.String())
		exp := expected[index]
		index++
		require.Equal(t, exp.method, r.Method)
		require.Equal(t, exp.path, r.URL.Path)
		require.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		require.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
		if exp.query != "" {
			requireQueryEqual(t, exp.query, r.URL.RawQuery)
		}
		if exp.response != "" {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(exp.status)
		_, _ = io.WriteString(w, exp.response)
	}))
	defer server.Close()

	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
		t.Setenv(key, "")
	}
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	root.SetArgs(append([]string{"--api-url", server.URL, "--api-key", "k", "-o", "table"}, args...))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := root.ExecuteContext(ctx)
	if err == nil {
		require.Equal(t, len(expected), index)
	}
	return stdout.String(), stderr.String(), err
}

type closingReadCloser struct {
	*strings.Reader
	closed chan struct{}
}

func (r *closingReadCloser) Close() error {
	select {
	case <-r.closed:
	default:
		close(r.closed)
	}
	return nil
}

// `image logs --follow --timeout` bounds the follow: a stream that never
// ends is cut at the deadline with exit 8, not left open forever.
func TestImageLogs_followHonoursTimeout(t *testing.T) {
	serverURL := ""
	hold := make(chan struct{})
	defer close(hold)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/images/11111111-1111-1111-1111-111111111111/build-logs-url":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"url":"`+serverURL+`/build-logs/images/11111111-1111-1111-1111-111111111111"}`)
		default:
			w.Header().Set("Content-Type", "text/plain")
			if f, ok := w.(http.Flusher); ok {
				_, _ = io.WriteString(w, "building...\n")
				f.Flush()
			}
			select {
			case <-hold:
			case <-r.Context().Done():
			}
		}
	}))
	defer srv.Close()
	serverURL = srv.URL
	_, errb, code := run(t, cfgDir(t), nil, "image", "logs", "11111111-1111-1111-1111-111111111111", "--follow", "--timeout", "700ms", "--api-url", srv.URL, "--api-key", "k", "--project", testProjP1)
	if code != cerr.ExitTimeout || !has(errb, "timed out after") {
		t.Fatalf("%d %s", code, errb)
	}
}

// The API types `anonymous` as a boolean with no "both" value, so --anonymous
// accepts only true/false and refuses anything else before any request.
func TestImageList_anonymousFilterIsBoolean(t *testing.T) {
	var queries []string
	var mu sync.Mutex
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		queries = append(queries, r.URL.Path+"?"+r.URL.RawQuery)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/regions" {
			_, _ = io.WriteString(w, `{"regions":[]}`)
			return
		}
		_, _ = io.WriteString(w, `{"images":[]}`)
	}))
	defer server.Close()
	runList := func(value string) (int, string) {
		t.Helper()
		_, errb, code := run(t, cfgDir(t), nil, "--api-url", server.URL, "--api-key", "k", "-o", "json", "image", "list", "--anonymous", value)
		return code, errb
	}

	for _, value := range []string{"any", "yes", ""} {
		mu.Lock()
		queries = nil
		mu.Unlock()
		code, errb := runList(value)
		if value == "" {
			require.Equal(t, 0, code, errb)
			continue
		}
		require.Equal(t, cerr.ExitUsage, code, errb)
		require.Contains(t, errb, "--anonymous wants true or false")
		mu.Lock()
		require.Empty(t, queries, "refused before any request")
		mu.Unlock()
	}

	mu.Lock()
	queries = nil
	mu.Unlock()
	code, errb := runList("TRUE")
	require.Equal(t, 0, code, errb)
	mu.Lock()
	defer mu.Unlock()
	require.Contains(t, queries, "/images?anonymous=true&limit=100")
}
