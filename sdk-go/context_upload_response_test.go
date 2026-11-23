// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestContextUpload_whenDeclarationIsMalformed(t *testing.T) {
	base := `{"id":"ctx","region_id":"region","state":"uploading","parts":[{"part_number":1,"bytes":3,"url":"https://storage.invalid/p"}]}`
	for _, test := range []struct {
		name, from, to string
	}{
		{"nonobject", base, `[]`},
		{"missing_id", `"id":"ctx",`, ``},
		{"empty_id", `"id":"ctx"`, `"id":""`},
		{"dot_id", `"id":"ctx"`, `"id":".."`},
		{"missing_region", `"region_id":"region",`, ``},
		{"wrong_region", `"region_id":"region"`, `"region_id":false`},
		{"missing_state", `"state":"uploading",`, ``},
		{"empty_state", `"state":"uploading"`, `"state":""`},
		{"missing_parts", `,"parts":[{"part_number":1,"bytes":3,"url":"https://storage.invalid/p"}]`, ``},
		{"null_parts", `[{"part_number":1,"bytes":3,"url":"https://storage.invalid/p"}]`, `null`},
		{"nonobject_part", `{"part_number":1,"bytes":3,"url":"https://storage.invalid/p"}`, `7`},
		{"boolean_number", `"part_number":1`, `"part_number":true`},
		{"fractional_number", `"part_number":1`, `"part_number":1.5`},
		{"unsafe_number", `"part_number":1`, `"part_number":9007199254740992`},
		{"negative_size", `"bytes":3`, `"bytes":-1`},
		{"zero_size", `"bytes":3`, `"bytes":0`},
		{"small_size", `"bytes":3`, `"bytes":2`},
		{"large_size", `"bytes":3`, `"bytes":4`},
		{"overflow_size", `"bytes":3`, `"bytes":9223372036854775807`},
		{"wrong_number", `"part_number":1`, `"part_number":2`},
		{"wrong_url", `"url":"https://storage.invalid/p"`, `"url":5`},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given a declaration breaking a field the uploader reads.
			answer := strings.Replace(base, test.from, test.to, 1)
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					if _, err := fmt.Fprint(w, answer); err != nil {
						t.Error(err)
					}
				},
			})
			client := newTestClient(t, api.Server)

			// When the archive is uploaded.
			_, err := (&Images{client: client}).uploadContext(t.Context(), contextTestArchive(t, []byte("abc")), "")

			// Then neither storage nor complete is reached.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, api.requests(), 1)
		})
	}
}

func TestContextUpload_whenReadyStillRequiresParts(t *testing.T) {
	// Given a ready response without the mandatory parts array.
	api := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts": jsonHandler(t, map[string]string{
			"id": "ctx", "region_id": "region", "state": "ready",
		}),
	})
	client := newTestClient(t, api.Server)

	// When upload reuse is attempted.
	_, err := (&Images{client: client}).uploadContext(t.Context(), contextTestArchive(t, []byte("abc")), "")

	// Then ready reuse cannot mask malformed fields.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Len(t, api.requests(), 1)
}

func TestContextUpload_whenStorageURLIsMalformed(t *testing.T) {
	for _, endpoint := range []string{
		"", "/relative", "//storage.invalid/p", "file:///secret", "https://",
		"https://user:password@storage.invalid/p", "https://storage.invalid/p#fragment",
		"https://storage.invalid/p#", "https://storage.invalid/a\\b", "https://storage.invalid/a b",
		"https://storage.invalid/%zz", "https://storage.invalid:bad/p",
	} {
		t.Run(fmt.Sprintf("%q", endpoint), func(t *testing.T) {
			// Given an invalid presigned storage URL.
			api := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /build-contexts": jsonHandler(t, contextTestDeclaration([]map[string]any{
					contextTestPart(1, 3, endpoint),
				})),
			})
			client := newTestClient(t, api.Server)

			// When the upload is attempted.
			_, err := (&Images{client: client}).uploadContext(t.Context(), contextTestArchive(t, []byte("abc")), "")

			// Then URL validation fails before storage and reveals no signed URL or parse cause.
			require.ErrorIs(t, err, ErrBadRequest)
			require.NoError(t, errors.Unwrap(err))
			require.NotContains(t, fmt.Sprintf("%+v", err), "password")
			require.Len(t, api.requests(), 1)
		})
	}
}

func TestContextUpload_whenResponseExceedsClientLimit(t *testing.T) {
	// Given an API response larger than the caller's buffered-response cap.
	api := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /build-contexts": jsonHandler(t, contextTestDeclaration([]map[string]any{})),
	})
	client := newTestClient(t, api.Server, WithMaxResponseBytes(16))

	// When the upload is attempted.
	_, err := (&Images{client: client}).uploadContext(t.Context(), contextTestArchive(t, []byte("abc")), "")

	// Then the shared transport enforces its cap.
	require.ErrorIs(t, err, ErrPayloadTooLarge)
	require.Len(t, api.requests(), 1)
}
