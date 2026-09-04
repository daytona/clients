// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package minio

import (
	"archive/tar"
	"bytes"
	"context"
	"errors"
	"io"
	"maps"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

const testOrgID = "test-org"

type fakeStorage struct {
	mu       sync.Mutex
	objects  map[string][]byte
	putCount int
	failPuts bool
}

func newFakeStorage(t *testing.T) (*Client, *fakeStorage) {
	t.Helper()

	storage := &fakeStorage{objects: map[string][]byte{}}
	server := httptest.NewServer(http.HandlerFunc(storage.serve))
	t.Cleanup(server.Close)

	endpoint, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("failed to parse test server url: %v", err)
	}

	client, err := NewClient(endpoint.Host, "access-key", "secret-key", "bucket", false, "")
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	return client, storage
}

func (f *fakeStorage) serve(w http.ResponseWriter, r *http.Request) {
	if r.URL.Query().Has("location") {
		w.Header().Set("Content-Type", "application/xml")
		_, _ = io.WriteString(w, `<?xml version="1.0" encoding="UTF-8"?><LocationConstraint>us-east-1</LocationConstraint>`)
		return
	}

	if r.Method != http.MethodPut {
		w.WriteHeader(http.StatusOK)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		return
	}

	if strings.HasPrefix(r.Header.Get("X-Amz-Content-Sha256"), "STREAMING-") {
		body, err = decodeAWSChunked(body)
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
	}

	f.mu.Lock()
	defer f.mu.Unlock()

	if f.failPuts {
		w.Header().Set("Content-Type", "application/xml")
		w.WriteHeader(http.StatusForbidden)
		_, _ = io.WriteString(w, `<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>`)
		return
	}

	f.putCount++
	f.objects[strings.TrimPrefix(r.URL.Path, "/")] = body
	w.Header().Set("ETag", `"d41d8cd98f00b204e9800998ecf8427e"`)
	w.WriteHeader(http.StatusOK)
}

// decodeAWSChunked unwraps the `aws-chunked` framing the storage client uses
// when streaming a signed payload over plain HTTP.
func decodeAWSChunked(body []byte) ([]byte, error) {
	payload := []byte{}
	for {
		end := bytes.Index(body, []byte("\r\n"))
		if end < 0 {
			return nil, errors.New("truncated chunk header")
		}

		sizeField, _, _ := strings.Cut(string(body[:end]), ";")
		size, err := strconv.ParseInt(sizeField, 16, 64)
		if err != nil {
			return nil, err
		}

		body = body[end+2:]
		if size == 0 {
			return payload, nil
		}
		if int64(len(body)) < size+2 {
			return nil, errors.New("truncated chunk body")
		}

		payload = append(payload, body[:size]...)
		body = body[size+2:]
	}
}

func (f *fakeStorage) setFailPuts(fail bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.failPuts = fail
}

func (f *fakeStorage) puts() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.putCount
}

func (f *fakeStorage) archives() map[string][]byte {
	f.mu.Lock()
	defer f.mu.Unlock()

	archives := map[string][]byte{}
	for name, content := range f.objects {
		if strings.HasSuffix(name, "/"+CONTEXT_TAR_FILE_NAME) {
			archives[name] = content
		}
	}
	return archives
}

func (f *fakeStorage) uploadedArchive(t *testing.T) []byte {
	t.Helper()

	archives := f.archives()
	if len(archives) != 1 {
		t.Fatalf("expected exactly one uploaded archive, got %d", len(archives))
	}
	for _, content := range archives {
		return content
	}
	return nil
}

// tarEntries tolerates a missing end-of-archive trailer: the tar writer is
// closed after the upload, so the uploaded bytes stop after the last entry.
// All entries themselves are complete and readable.
func tarEntries(t *testing.T, archive []byte) map[string]string {
	t.Helper()

	entries := map[string]string{}
	reader := tar.NewReader(bytes.NewReader(archive))
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return entries
		}
		if err != nil {
			t.Fatalf("failed to read archive: %v", err)
		}

		content, err := io.ReadAll(reader)
		if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) {
			t.Fatalf("failed to read archive entry %s: %v", header.Name, err)
		}
		entries[header.Name] = string(content)
	}
}

func tarHeader(t *testing.T, archive []byte, name string) *tar.Header {
	t.Helper()

	reader := tar.NewReader(bytes.NewReader(archive))
	for {
		header, err := reader.Next()
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			t.Fatalf("archive has no entry %q", name)
		}
		if err != nil {
			t.Fatalf("failed to read archive: %v", err)
		}
		if header.Name == name {
			return header
		}
	}
}

func writeFile(t *testing.T, dir, name, content string) {
	t.Helper()

	if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
		t.Fatalf("failed to write %s: %v", name, err)
	}
}

func assertNoContextArchives(t *testing.T, dir string) {
	t.Helper()

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("failed to read %s: %v", dir, err)
	}
	for _, entry := range entries {
		if isContextArchiveFile(entry.Name()) {
			t.Errorf("context archive %q was left behind in %s", entry.Name(), dir)
		}
	}
}

func TestProcessDirectoryProducesStableHashAcrossRuns(t *testing.T) {
	client, _ := newFakeStorage(t)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	t.Chdir(t.TempDir())

	first, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{})
	if err != nil {
		t.Fatalf("first ProcessDirectory returned an error: %v", err)
	}

	// A run must not change the context directory in a way that alters the next
	// run's hash, otherwise an unchanged context is re-uploaded every time.
	time.Sleep(1100 * time.Millisecond)

	second, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{})
	if err != nil {
		t.Fatalf("second ProcessDirectory returned an error: %v", err)
	}

	if first[0] != second[0] {
		t.Errorf("hash of an unchanged context changed between runs: %s then %s", first[0], second[0])
	}
}

func TestProcessDirectoryKeepsRootEntryTypeForSymlinkedContext(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("creating symlinks requires elevated privileges on Windows")
	}

	client, storage := newFakeStorage(t)

	root := t.TempDir()
	realDir := filepath.Join(root, "real")
	if err := os.MkdirAll(realDir, 0o755); err != nil {
		t.Fatalf("failed to create context directory: %v", err)
	}
	writeFile(t, realDir, "Dockerfile", "FROM alpine\n")

	link := filepath.Join(root, "link")
	if err := os.Symlink(realDir, link); err != nil {
		t.Fatalf("failed to create symlink: %v", err)
	}
	t.Chdir(t.TempDir())

	if _, err := client.ProcessDirectory(context.Background(), link, testOrgID, map[string]bool{}); err != nil {
		t.Fatalf("ProcessDirectory returned an error: %v", err)
	}

	// The walk reports the root as a symlink, so the archive must record it as
	// one. Recording the target's directory metadata instead would replace the
	// entry with an empty directory. Resolving symlinked context roots so their
	// contents are archived is a separate, pre-existing gap.
	header := tarHeader(t, storage.uploadedArchive(t), ".")
	if header.Typeflag != tar.TypeSymlink {
		t.Errorf("root entry typeflag = %q, want %q", header.Typeflag, tar.TypeSymlink)
	}
}

func TestProcessDirectoryIncludesNestedFilesNamedLikeScratchArchives(t *testing.T) {
	client, storage := newFakeStorage(t)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	nested := filepath.Join(dir, "fixtures")
	if err := os.MkdirAll(nested, 0o755); err != nil {
		t.Fatalf("failed to create nested directory: %v", err)
	}
	writeFile(t, nested, contextTarPrefix+"sample.tar", "user fixture")
	t.Chdir(t.TempDir())

	if _, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{}); err != nil {
		t.Fatalf("ProcessDirectory returned an error: %v", err)
	}

	entries := tarEntries(t, storage.uploadedArchive(t))
	nestedEntry := filepath.Join("fixtures", contextTarPrefix+"sample.tar")
	if got, found := entries[nestedEntry]; !found || got != "user fixture" {
		t.Errorf("nested file %s should be included in the context, entries = %v", nestedEntry, slices.Sorted(maps.Keys(entries)))
	}
}

func TestProcessDirectoryPreservesExistingContextTar(t *testing.T) {
	client, storage := newFakeStorage(t)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	writeFile(t, dir, CONTEXT_TAR_FILE_NAME, "user owned archive")

	// The command is commonly run from the context directory itself.
	t.Chdir(dir)

	if _, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{}); err != nil {
		t.Fatalf("ProcessDirectory returned an error: %v", err)
	}

	content, err := os.ReadFile(filepath.Join(dir, CONTEXT_TAR_FILE_NAME))
	if err != nil {
		t.Fatalf("failed to read %s: %v", CONTEXT_TAR_FILE_NAME, err)
	}
	if string(content) != "user owned archive" {
		t.Errorf("%s content = %q, want %q", CONTEXT_TAR_FILE_NAME, content, "user owned archive")
	}

	entries := tarEntries(t, storage.uploadedArchive(t))
	if _, found := entries[CONTEXT_TAR_FILE_NAME]; found {
		t.Errorf("%s should stay excluded from the uploaded context", CONTEXT_TAR_FILE_NAME)
	}
}

func TestProcessDirectoryConcurrentRunsDoNotCollide(t *testing.T) {
	client, storage := newFakeStorage(t)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	writeFile(t, dir, "app.txt", "application source")
	t.Chdir(t.TempDir())

	var wg sync.WaitGroup
	errs := make([]error, 2)
	for i := range errs {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			_, errs[index] = client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{})
		}(i)
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("concurrent run %d returned an error: %v", i, err)
		}
	}

	archives := storage.archives()
	if len(archives) == 0 {
		t.Fatal("expected at least one uploaded archive")
	}
	for name, archive := range archives {
		entries := tarEntries(t, archive)
		for _, want := range []string{"Dockerfile", "app.txt"} {
			if _, found := entries[want]; !found {
				t.Errorf("archive %s is missing %s", name, want)
			}
		}
		for entry := range entries {
			if isContextArchiveFile(filepath.Base(entry)) {
				t.Errorf("archive %s contains scratch archive %s", name, entry)
			}
		}
	}

	assertNoContextArchives(t, dir)
}

func TestProcessDirectoryCleansUpOnCacheHit(t *testing.T) {
	client, storage := newFakeStorage(t)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	workingDir := t.TempDir()
	t.Chdir(workingDir)

	primed, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{})
	if err != nil {
		t.Fatalf("priming ProcessDirectory returned an error: %v", err)
	}

	uploadsBefore := storage.puts()
	existingObjects := map[string]bool{testOrgID + "/" + primed[0]: true}

	cached, err := client.ProcessDirectory(context.Background(), dir, testOrgID, existingObjects)
	if err != nil {
		t.Fatalf("cached ProcessDirectory returned an error: %v", err)
	}
	if cached[0] != primed[0] {
		t.Fatalf("context hash changed between runs: %s then %s", primed[0], cached[0])
	}
	if uploads := storage.puts() - uploadsBefore; uploads != 0 {
		t.Errorf("expected no uploads on a cache hit, got %d", uploads)
	}

	assertNoContextArchives(t, dir)
	assertNoContextArchives(t, workingDir)
}

func TestProcessDirectoryCleansUpOnError(t *testing.T) {
	client, storage := newFakeStorage(t)
	storage.setFailPuts(true)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	workingDir := t.TempDir()
	t.Chdir(workingDir)

	if _, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{}); err == nil {
		t.Fatal("expected ProcessDirectory to fail when the upload fails")
	}

	assertNoContextArchives(t, dir)
	assertNoContextArchives(t, workingDir)
}

func TestProcessDirectorySkipsStaleScratchArchives(t *testing.T) {
	client, storage := newFakeStorage(t)

	dir := t.TempDir()
	writeFile(t, dir, "Dockerfile", "FROM alpine\n")
	writeFile(t, dir, contextTarPrefix+"stale.tar", "archive from an interrupted run")
	t.Chdir(t.TempDir())

	if _, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{}); err != nil {
		t.Fatalf("ProcessDirectory returned an error: %v", err)
	}

	entries := tarEntries(t, storage.uploadedArchive(t))
	if _, found := entries[contextTarPrefix+"stale.tar"]; found {
		t.Errorf("stale scratch archive %s was included in the uploaded context", contextTarPrefix+"stale.tar")
	}
	if _, found := entries["Dockerfile"]; !found {
		t.Error("uploaded context is missing Dockerfile")
	}

	content, err := os.ReadFile(filepath.Join(dir, contextTarPrefix+"stale.tar"))
	if err != nil {
		t.Fatalf("failed to read stale scratch archive: %v", err)
	}
	if string(content) != "archive from an interrupted run" {
		t.Errorf("stale scratch archive content = %q, want it unchanged", content)
	}
}

func TestProcessDirectoryDoesNotWriteToWorkingDirectory(t *testing.T) {
	tests := []struct {
		name        string
		failUploads bool
	}{
		{name: "upload succeeds", failUploads: false},
		{name: "upload fails", failUploads: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			client, storage := newFakeStorage(t)
			storage.setFailPuts(tt.failUploads)

			dir := t.TempDir()
			writeFile(t, dir, "Dockerfile", "FROM alpine\n")
			workingDir := t.TempDir()
			t.Chdir(workingDir)

			_, err := client.ProcessDirectory(context.Background(), dir, testOrgID, map[string]bool{})
			if tt.failUploads && err == nil {
				t.Fatal("expected ProcessDirectory to fail when the upload fails")
			}
			if !tt.failUploads && err != nil {
				t.Fatalf("ProcessDirectory returned an error: %v", err)
			}

			left, err := os.ReadDir(workingDir)
			if err != nil {
				t.Fatalf("failed to read working directory: %v", err)
			}
			if len(left) != 0 {
				names := make([]string, 0, len(left))
				for _, entry := range left {
					names = append(names, entry.Name())
				}
				t.Errorf("working directory should stay untouched, found %v", names)
			}
		})
	}
}
