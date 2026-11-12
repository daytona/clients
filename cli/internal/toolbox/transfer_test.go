// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"errors"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

var uploadFileStubMu sync.Mutex

type blockingReader struct {
	started chan struct{}
	release chan struct{}
	read    bool
	mu      sync.Mutex
}

type failingReadCloser struct{}

func (failingReadCloser) Read([]byte) (int, error) { return 0, errors.New("producer read failed") }
func (failingReadCloser) Close() error             { return nil }

func (r *blockingReader) Read(p []byte) (int, error) {
	r.mu.Lock()
	if !r.read {
		r.read = true
		close(r.started)
	}
	r.mu.Unlock()
	<-r.release
	copy(p, []byte("payload"))
	return len("payload"), io.EOF
}

func TestDownloadFile_success(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("file-content"))
	}))
	defer srv.Close()

	dst := filepath.Join(t.TempDir(), "out.txt")
	if err := transferClient().DownloadFile(context.Background(), srv.URL+"/files/download", dst); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "file-content" {
		t.Fatalf("got %q", got)
	}
}

func TestDownloadFile_4xxMapsError(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"message":"not found"}`))
	}))
	defer srv.Close()

	dst := filepath.Join(t.TempDir(), "out.txt")
	err := transferClient().DownloadFile(context.Background(), srv.URL+"/files/download", dst)
	if err == nil {
		t.Fatal("expected error")
	}
	var ce *cerr.Error
	if !errors.As(err, &ce) {
		t.Fatalf("expected *cerr.Error, got %T", err)
	}
	if ce.Code != cerr.ExitNotFound {
		t.Fatalf("code=%d want %d", ce.Code, cerr.ExitNotFound)
	}
}

func TestDownloadFile_createsParentDirs(t *testing.T) {
	t.Parallel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()

	dst := filepath.Join(t.TempDir(), "a", "b", "c.txt")
	if err := transferClient().DownloadFile(context.Background(), srv.URL, dst); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dst); err != nil {
		t.Fatal(err)
	}
}

func TestUploadFile_success(t *testing.T) {
	t.Parallel()
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	var receivedFilename string
	var receivedBody string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ct := r.Header.Get("Content-Type")
		mediaType, params, err := mime.ParseMediaType(ct)
		if err != nil || !strings.HasPrefix(mediaType, "multipart/") {
			http.Error(w, "bad content-type", http.StatusBadRequest)
			return
		}
		mr := multipart.NewReader(r.Body, params["boundary"])
		part, err := mr.NextPart()
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		receivedFilename = part.FileName()
		data, _ := io.ReadAll(part)
		receivedBody = string(data)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	localFile := filepath.Join(t.TempDir(), "upload.txt")
	if err := os.WriteFile(localFile, []byte("upload-data"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := transferClient().UploadFile(context.Background(), srv.URL, localFile, "/remote/path/upload.txt"); err != nil {
		t.Fatal(err)
	}
	if receivedFilename != "upload.txt" {
		t.Fatalf("filename=%q", receivedFilename)
	}
	if receivedBody != "upload-data" {
		t.Fatalf("body=%q", receivedBody)
	}
}

func TestUploadFile_4xxMapsError(t *testing.T) {
	t.Parallel()
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"message":"denied"}`))
	}))
	defer srv.Close()

	localFile := filepath.Join(t.TempDir(), "upload.txt")
	_ = os.WriteFile(localFile, []byte("x"), 0o644)
	err := transferClient().UploadFile(context.Background(), srv.URL, localFile, "/remote/upload.txt")
	if err == nil {
		t.Fatal("expected error")
	}
	var ce *cerr.Error
	if !errors.As(err, &ce) {
		t.Fatalf("expected *cerr.Error, got %T", err)
	}
	if ce.Code != cerr.ExitForbidden {
		t.Fatalf("code=%d want %d", ce.Code, cerr.ExitForbidden)
	}
}

func TestUploadFile_missingLocalFile(t *testing.T) {
	t.Parallel()
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("should not reach server")
	}))
	defer srv.Close()

	err := transferClient().UploadFile(context.Background(), srv.URL, "/nonexistent/file.txt", "/remote/file.txt")
	if err == nil {
		t.Fatal("expected error for missing file")
	}
	if !strings.Contains(err.Error(), "/nonexistent/file.txt") {
		t.Fatalf("err=%v", err)
	}
}

func TestStartSignedUploadBody_multipartShape(t *testing.T) {
	t.Parallel()
	body, contentType, done := startSignedUploadBody("local.txt", "/remote/dest.txt", strings.NewReader("payload"))
	if !strings.Contains(contentType, "multipart/form-data") {
		t.Fatalf("ct=%q", contentType)
	}
	_, params, _ := mime.ParseMediaType(contentType)
	mr := multipart.NewReader(body, params["boundary"])
	part, err := mr.NextPart()
	if err != nil {
		t.Fatal(err)
	}
	if part.FileName() != "dest.txt" {
		t.Fatalf("filename=%q", part.FileName())
	}
	data, _ := io.ReadAll(part)
	if string(data) != "payload" {
		t.Fatalf("data=%q", data)
	}
	require.NoError(t, <-done)
}

func TestStartSignedUploadBody_streamsWithoutReadingWholeInputFirst(t *testing.T) {
	t.Parallel()
	src := &blockingReader{started: make(chan struct{}), release: make(chan struct{})}
	body, contentType, done := startSignedUploadBody("local.txt", "/remote/dest.txt", src)
	require.Contains(t, contentType, "multipart/form-data")
	_, params, _ := mime.ParseMediaType(contentType)
	readDone := make(chan string, 1)
	go func() {
		mr := multipart.NewReader(body, params["boundary"])
		part, partErr := mr.NextPart()
		if partErr != nil {
			readDone <- "error: " + partErr.Error()
			return
		}
		data, readErr := io.ReadAll(part)
		if readErr != nil {
			readDone <- "error: " + readErr.Error()
			return
		}
		readDone <- string(data)
	}()
	<-src.started
	select {
	case got := <-readDone:
		t.Fatalf("reader finished before source released: %s", got)
	default:
	}
	close(src.release)
	require.Equal(t, "payload", <-readDone)
	require.NoError(t, <-done)
}

func TestNewSignedUploadRequest_closesProducerWhenRequestBuildFails(t *testing.T) {
	t.Parallel()
	_, done, err := newSignedUploadRequest(context.Background(), "://bad", "local.txt", "/remote/upload.txt", strings.NewReader("payload"))
	require.Error(t, err)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("upload producer did not exit after request-build failure")
	}
}

func TestUploadFile_surfacesProducerErrorEvenWhenServerRepliesEarly(t *testing.T) {
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	old := openUploadFile
	openUploadFile = func(string) (io.ReadCloser, error) {
		return failingReadCloser{}, nil
	}
	defer func() { openUploadFile = old }()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	err := transferClient().UploadFile(context.Background(), srv.URL, "/tmp/unused", "/remote/file.txt")
	require.Error(t, err)
	require.Contains(t, err.Error(), "producer read failed")
}

func TestUploadReader_prefersMappedHTTPErrorWhenServerRejectsEarly(t *testing.T) {
	t.Parallel()
	payload := strings.NewReader(strings.Repeat("x", 4<<20))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"message":"denied"}`))
	}))
	defer srv.Close()

	err := transferClient().UploadReader(context.Background(), srv.URL, "archive.tar", "/remote/archive.tar", payload)
	require.Error(t, err)
	var ce *cerr.Error
	require.ErrorAs(t, err, &ce)
	require.Equal(t, cerr.ExitForbidden, ce.Code)
}

func TestDownloadRefusesSymlinkDestination(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("payload")) }))
	defer srv.Close()
	dir := t.TempDir()
	victim := filepath.Join(dir, "victim.txt")
	if err := os.WriteFile(victim, []byte("original"), 0o644); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "dest")
	if err := os.Symlink(victim, link); err != nil {
		t.Skip("symlinks unsupported")
	}
	err := transferClient().DownloadFile(context.Background(), srv.URL+"/f", link)
	if err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("download through a symlink accepted: %v", err)
	}
	if b, _ := os.ReadFile(victim); string(b) != "original" {
		t.Fatal("victim file was overwritten")
	}
}

func transferClient() *Client {
	return New(api.New("http://unused.invalid", "tok", "test"))
}

// Signed-URL transfers hit the toolbox host like every other request and must
// carry the client-identity triple.
func TestSignedTransfersCarryIdentityHeaders(t *testing.T) {
	t.Parallel()
	var mu sync.Mutex
	var seen []http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.Header.Clone())
		mu.Unlock()
		_, _ = io.Copy(io.Discard, r.Body)
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()

	dst := filepath.Join(t.TempDir(), "out")
	require.NoError(t, transferClient().DownloadFile(context.Background(), srv.URL+"/files/download", dst))
	require.NoError(t, transferClient().UploadReader(context.Background(), srv.URL, "a.txt", "/remote/a.txt", strings.NewReader("x")))

	mu.Lock()
	defer mu.Unlock()
	require.Len(t, seen, 2)
	for _, h := range seen {
		require.Equal(t, "cli", h.Get("X-Daytona-Source"))
		require.Equal(t, "test", h.Get("X-Daytona-SDK-Version"))
		require.Equal(t, "cli/test", h.Get("User-Agent"))
	}
}

// A failed upload shows the toolbox's message and code, not a bare status.
func TestUploadFile_errorBodyIsDecoded(t *testing.T) {
	t.Parallel()
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"code":"BAD_REQUEST","message":"File exists (os error 17)","source":"DAYTONA_DAEMON"}`))
	}))
	defer srv.Close()
	localFile := filepath.Join(t.TempDir(), "upload.txt")
	require.NoError(t, os.WriteFile(localFile, []byte("x"), 0o644))
	err := transferClient().UploadFile(context.Background(), srv.URL, localFile, "/remote/dir")
	require.Error(t, err)
	require.Contains(t, err.Error(), "File exists (os error 17)")
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

// A transport error never prints a pre-signed URL's signature.
func TestSignedTransferErrors_redactTheSignedQuery(t *testing.T) {
	t.Parallel()
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	dead := srv.URL + "/toolbox/sbx/files/upload?path=%2Ftmp%2Fx&expires=1&signature=SECRET-SIGNATURE"
	srv.Close()
	localFile := filepath.Join(t.TempDir(), "upload.txt")
	require.NoError(t, os.WriteFile(localFile, []byte("x"), 0o644))
	err := transferClient().UploadFile(context.Background(), dead, localFile, "/tmp/x")
	require.Error(t, err)
	require.NotContains(t, err.Error(), "SECRET-SIGNATURE")
	require.Contains(t, err.Error(), "/toolbox/sbx/files/upload")
	err = transferClient().DownloadFile(context.Background(), dead, filepath.Join(t.TempDir(), "out"))
	require.Error(t, err)
	require.NotContains(t, err.Error(), "SECRET-SIGNATURE")
}

func TestSignedTransfer_invalidURLIsNotPrinted(t *testing.T) {
	t.Parallel()
	bad := "http://proxy.example/files\x7f?signature=SECRET-SIGNATURE"
	err := transferClient().DownloadFile(context.Background(), bad, filepath.Join(t.TempDir(), "out"))
	require.Error(t, err)
	require.NotContains(t, err.Error(), "SECRET-SIGNATURE")
	uploadFileStubMu.Lock()
	defer uploadFileStubMu.Unlock()
	localFile := filepath.Join(t.TempDir(), "upload.txt")
	require.NoError(t, os.WriteFile(localFile, []byte("x"), 0o644))
	err = transferClient().UploadFile(context.Background(), bad, localFile, "/tmp/x")
	require.Error(t, err)
	require.NotContains(t, err.Error(), "SECRET-SIGNATURE")
}
