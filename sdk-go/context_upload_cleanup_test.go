// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestContextUpload_whenStorageResponseIsHugeAndUnfinished(t *testing.T) {
	// Given storage answering success headers with an unbounded, unfinished body.
	closed := make(chan struct{})
	server := contextStorageServer(t, func(w http.ResponseWriter, r *http.Request) {
		contextDrainRequest(t, r)
		w.Header().Set("Content-Length", "1099511627776")
		w.WriteHeader(http.StatusOK)
		if err := http.NewResponseController(w).Flush(); err != nil {
			t.Error(err)
			return
		}
		<-r.Context().Done()
		close(closed)
	})
	client := newTestClient(t, server, WithMaxResponseBytes(1))
	uploader := contextTestUploader(t, client, []byte("abc"))

	// When the part's success headers arrive.
	err := uploader.putPart(t.Context(), contextUploadPart{number: 1, bytes: 3, url: server.URL + "/part"})

	// Then the body is closed unread; neither buffering nor the ordinary response cap applies.
	require.NoError(t, err)
	select {
	case <-closed:
	case <-time.After(5 * time.Second):
		t.Fatal("storage response was not closed")
	}
}

func TestContextUpload_whenEveryPartURLIsCheckedBeforeSending(t *testing.T) {
	// Given an otherwise valid first part followed by an invalid URL.
	api := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts": jsonHandler(t, contextTestDeclaration([]map[string]any{
			contextTestPart(1, 1, "https://storage.invalid/part/1"),
			contextTestPart(2, 2, "file:///secret"),
		})),
	})
	client := newTestClient(t, api.Server)

	// When the upload layout is checked before any workers start.
	_, err := (&Images{client: client}).uploadContext(t.Context(), contextTestArchive(t, []byte("abc")), "")

	// Then the entire declaration is rejected before any parts or completion.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Len(t, api.requests(), 1)
}

func TestContextUpload_whenFutureStateAndIntegralJSONNumbers(t *testing.T) {
	// Given an unfamiliar state and JSON integers spelled with zero fractions.
	api := newFakeServer(t, nil)
	raw := json.RawMessage(`{"id":"ctx","region_id":"r","state":"new-state",
		"parts":[{"part_number":1.0,"bytes":3e0,"url":"` + api.Server.URL + `/part"}]}`)

	// When the declaration is parsed and laid out.
	declaration, err := parseContextUpload(raw)
	require.NoError(t, err)
	parts, err := contextUploadLayout(declaration.parts, 3)

	// Then unknown states retain the upload path and numeric parsing matches TypeScript.
	require.NoError(t, err)
	require.False(t, declaration.ready)
	require.Len(t, parts, 1)
	require.EqualValues(t, 3, parts[0].bytes)
}

func TestContextUpload_whenArchiveBodyMustCloseAfterFailure(t *testing.T) {
	// Given a transport that fails after reading the request, without closing it itself.
	var body io.ReadCloser
	transport := contextTestTransport(func(r *http.Request) (*http.Response, error) {
		body = r.Body
		return nil, errors.New("connection failed")
	})
	client, err := New(WithAPIKey("key"), WithHTTPClient(&http.Client{Transport: transport}))
	require.NoError(t, err)
	defer func() { require.NoError(t, client.Close()) }()
	uploader := contextTestUploader(t, client, []byte("abc"))
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	uploader.pause = func(ctx context.Context, _ time.Duration) error {
		cancel()
		return transportError(ctx, ctx.Err())
	}

	// When a failed attempt is canceled before it can retry.
	err = uploader.putPart(ctx, contextUploadPart{number: 1, bytes: 3, url: "https://storage.invalid/part"})

	// Then the attempt owns cleanup even for a custom transport.
	require.ErrorIs(t, err, context.Canceled)
	fileBody, ok := body.(*contextPartBody)
	require.True(t, ok)
	_, statErr := fileBody.file.Stat()
	require.ErrorIs(t, statErr, os.ErrClosed)
	require.NoError(t, body.Close(), "request cleanup is idempotent")
}

func TestContextUpload_whenLocalOrBlankRegion(t *testing.T) {
	for _, region := range []string{"", "  ", "LOCAL", " local "} {
		t.Run(strings.ReplaceAll(region, " ", "_"), func(t *testing.T) {
			// Given default-region aliases accepted by the image builder.
			answer := contextTestDeclaration([]map[string]any{})
			answer["state"] = "ready"
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": jsonHandler(t, answer),
			})
			client := newTestClient(t, api.Server)

			// When a context is uploaded.
			result, err := (&Images{client: client}).uploadContext(t.Context(), contextTestArchive(t, []byte("abc")), region)

			// Then no requested region is sent; the actual region remains available.
			require.NoError(t, err)
			require.Equal(t, "resolved-region", result.Region)
			require.NotContains(t, string(api.requests()[0].Body), "region_id")
		})
	}
}
