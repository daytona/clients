// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"bytes"
	"context"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestFilesystem(t *testing.T) {
	// Given: one isolated sandbox, always removed by rawSandbox's cleanup.
	client := newToolboxClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-fs"))
	sb, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	fs := sb.FS()
	root := "/tmp/" + uniqueName(t, "files")
	require.NoError(t, fs.Mkdir(ctx, root, nil))

	t.Run("metadata and mutations", func(t *testing.T) {
		// When: write, inspect, change permissions, move, and remove.
		path := root + "/a.txt"
		require.NoError(t, fs.WriteFile(ctx, path, []byte("alpha\nbeta\n"), &daytona.WriteFileOptions{Atomic: true}))
		data, readErr := fs.ReadFile(ctx, path, nil)
		require.NoError(t, readErr)
		require.Equal(t, "alpha\nbeta\n", string(data))
		info, statErr := fs.Stat(ctx, path)
		require.NoError(t, statErr)
		require.Equal(t, int64(len(data)), info.Size)
		page, listErr := fs.List(ctx, root, nil)
		require.NoError(t, listErr)
		require.Len(t, page.Items, 1)
		require.NoError(t, fs.Chmod(ctx, path, &daytona.FilePermissionsOptions{Mode: "0640"}))
		require.NoError(t, fs.Move(ctx, path, root+"/moved"))
		require.NoError(t, fs.Remove(ctx, root+"/moved", nil))
		// Then: a missing read is the typed public error.
		_, missingErr := fs.ReadFile(ctx, root+"/missing", nil)
		require.ErrorIs(t, missingErr, daytona.ErrFileNotFound)
	})

	t.Run("bulk and search", func(t *testing.T) {
		// When: upload a batch and search names separately from contents.
		result, writeErr := fs.WriteMany(ctx, map[string][]byte{
			root + "/one.txt": []byte("needle"), root + "/two.csv": []byte("other"),
		}, &daytona.WriteManyOptions{Atomic: true})
		require.NoError(t, writeErr)
		require.Empty(t, result.Errors)
		require.Len(t, result.Files, 2)
		bulk, readErr := fs.ReadMany(ctx, []string{root + "/one.txt", root + "/missing"}, nil)
		require.NoError(t, readErr)
		require.Equal(t, []byte("needle"), bulk.Files[root+"/one.txt"])
		require.Len(t, bulk.Errors, 1)
		names, globErr := fs.Glob(ctx, root, "*.csv")
		require.NoError(t, globErr)
		require.Equal(t, []string{root + "/two.csv"}, names.Items)
		matches, grepErr := fs.Grep(ctx, root, "needle")
		require.NoError(t, grepErr)
		require.Len(t, matches.Items, 1)
		replaced, replaceErr := fs.Replace(ctx, []string{root + "/one.txt"}, "needle", "thread")
		require.NoError(t, replaceErr)
		require.True(t, replaced.Items[0].Success)
		// Then: replacement changed the content.
		text, textErr := fs.ReadText(ctx, root+"/one.txt", nil)
		require.NoError(t, textErr)
		require.Equal(t, "thread", text)
	})

	t.Run("files and streams", func(t *testing.T) {
		// Given
		local := filepath.Join(t.TempDir(), "source.csv")
		content := []byte("a,b\n1,2\n")
		require.NoError(t, os.WriteFile(local, content, 0o600))
		// When
		require.NoError(t, fs.UploadFile(ctx, local, root+"/upload.csv", nil))
		target := filepath.Join(t.TempDir(), "download.csv")
		require.NoError(t, fs.DownloadFile(ctx, root+"/upload.csv", target, nil))
		require.NoError(t, fs.Upload(ctx, root+"/stream", strings.NewReader("streamed"), nil))
		var output bytes.Buffer
		require.NoError(t, fs.Download(ctx, root+"/stream", &output, nil))
		// Then
		downloaded, readErr := os.ReadFile(target)
		require.NoError(t, readErr)
		require.Equal(t, content, downloaded)
		require.Equal(t, "streamed", output.String())
	})

	t.Run("signed round trip", func(t *testing.T) {
		// Given: no authentication headers on requests to signed URLs.
		options := &daytona.PresignedFileURLOptions{ExpiresIn: time.Minute}
		uploadURL, signErr := fs.PresignedUploadURL(ctx, root+"/signed", options)
		require.NoError(t, signErr)
		var body bytes.Buffer
		form := multipart.NewWriter(&body)
		part, partErr := form.CreateFormFile("file", "signed")
		require.NoError(t, partErr)
		_, writeErr := io.WriteString(part, "signed data")
		require.NoError(t, writeErr)
		require.NoError(t, form.Close())
		request, requestErr := http.NewRequestWithContext(ctx, http.MethodPost, uploadURL, &body)
		require.NoError(t, requestErr)
		request.Header.Set("Content-Type", form.FormDataContentType())
		httpClient := &http.Client{Timeout: time.Minute, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}}
		// When
		response, sendErr := httpClient.Do(request)
		require.NoError(t, sendErr)
		require.NoError(t, response.Body.Close())
		skipIfSignatureRefused(t, response.StatusCode)
		require.Less(t, response.StatusCode, 300)
		downloadURL, downloadSignErr := fs.PresignedDownloadURL(ctx, root+"/signed", options)
		require.NoError(t, downloadSignErr)
		download, downloadRequestErr := http.NewRequestWithContext(ctx, http.MethodGet, downloadURL, nil)
		require.NoError(t, downloadRequestErr)
		readResponse, downloadErr := httpClient.Do(download)
		require.NoError(t, downloadErr)
		defer func() { require.NoError(t, readResponse.Body.Close()) }()
		data, readErr := io.ReadAll(io.LimitReader(readResponse.Body, 1024))
		require.NoError(t, readErr)
		skipIfSignatureRefused(t, readResponse.StatusCode)
		// Then
		require.Equal(t, http.StatusOK, readResponse.StatusCode)
		require.Equal(t, "signed data", string(data))
	})

	t.Run("watch", func(t *testing.T) {
		// Given
		watchCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		defer cancel()
		watch, watchErr := fs.Watch(watchCtx, root, nil)
		require.NoError(t, watchErr)
		defer watch.Stop()
		// When
		require.NoError(t, fs.WriteText(ctx, root+"/watched", "event", nil))
		var observed bool
		for event, eventErr := range watch.Events(watchCtx) {
			require.NoError(t, eventErr)
			if strings.HasSuffix(event.Name, "watched") {
				observed = true
				break
			}
		}
		// Then
		require.True(t, observed)
		require.NoError(t, watch.Wait(ctx))
	})
	require.NoError(t, fs.Remove(ctx, root, &daytona.RemoveFileOptions{Recursive: true}))
}

// skipIfSignatureRefused skips when the deployment's proxy refuses a freshly
// minted, unexpired signature. The SDK built the URL the API returned; a 401
// here is a deployment problem the SDK cannot fix, so the test reports it
// instead of failing. Any other status is still checked by the caller.
func skipIfSignatureRefused(t *testing.T, status int) {
	t.Helper()
	if status == http.StatusUnauthorized {
		t.Skip("this deployment's proxy refused a freshly signed file URL (HTTP 401)")
	}
}
