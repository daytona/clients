// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestWriteMany_whenAtomic_uploadsExactParts(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-upload": jsonHandler(t,
		map[string]any{"files": []any{map[string]any{"path": "/a", "bytes": 3}}, "errors": []string{}})})
	fs := newFileTestService(t, fake.Server)
	// When
	result, err := fs.WriteMany(t.Context(), map[string][]byte{"/a": {0, 1, 255}}, &WriteManyOptions{Atomic: true})
	// Then
	require.NoError(t, err)
	require.Equal(t, []BulkWriteFile{{Path: "/a", Bytes: 3}}, result.Files)
	request := fake.requests()[0]
	require.Equal(t, "true", request.Query.Get("atomic"))
	_, params, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	require.NoError(t, err)
	reader := multipart.NewReader(bytes.NewReader(request.Body), params["boundary"])
	form, err := reader.ReadForm(1024)
	require.NoError(t, err)
	defer func() { require.NoError(t, form.RemoveAll()) }()
	require.Equal(t, []string{"/a"}, form.Value["files[0].path"])
	file, err := form.File["files[0].file"][0].Open()
	require.NoError(t, err)
	defer func() { require.NoError(t, file.Close()) }()
	data, err := io.ReadAll(file)
	require.NoError(t, err)
	require.Equal(t, []byte{0, 1, 255}, data)
}

func TestReadMany_whenMultipart_preservesPathsAndErrors(t *testing.T) {
	for _, contentType := range []string{"", `multipart/form-data; boundary="DAYTONA-FILE-BOUNDARY"`} {
		t.Run(contentType, func(t *testing.T) {
			// Given: a boundary-like sequence is file data, not a delimiter.
			body := "--DAYTONA-FILE-BOUNDARY\r\nContent-Disposition: form-data; name=\"file\"; filename*=utf-8''%2Fa%20b\r\n\r\n" +
				"\x00\xff\r\n--DAYTONA-FILE-BOUNDARY-not-a-delimiter\r\n--DAYTONA-FILE-BOUNDARY\r\n" +
				"Content-Disposition: form-data; name=\"error\"\r\n\r\n" +
				`{"path":"/missing","message":"absent","code":"FILE_NOT_FOUND","source":"TOOLBOX"}` +
				"\r\n--DAYTONA-FILE-BOUNDARY--\r\n"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-download": func(w http.ResponseWriter, _ *http.Request) {
				if contentType != "" {
					w.Header().Set("Content-Type", contentType)
				}
				if _, err := io.WriteString(w, body); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)
			// When
			result, err := fs.ReadMany(t.Context(), []string{"/a b", "/missing"}, nil)
			// Then
			require.NoError(t, err)
			require.Equal(t, []byte("\x00\xff\r\n--DAYTONA-FILE-BOUNDARY-not-a-delimiter"), result.Files["/a b"])
			require.Equal(t, []BulkReadError{{Path: "/missing", Message: "absent", Code: "FILE_NOT_FOUND", Source: "TOOLBOX"}}, result.Errors)
			require.JSONEq(t, `{"paths":["/a b","/missing"]}`, string(fake.requests()[0].Body))
		})
	}
}

func TestReadMany_whenMalformedPart(t *testing.T) {
	for _, tc := range []struct{ name, disposition, data string }{
		{"missing filename", `name="file"`, "data"},
		{"bad escape", `name="file"; filename*=utf-8''%GG`, "data"},
		{"bad utf8 name", `name="file"; filename*=utf-8''%FF`, "data"},
		{"bad error", `name="error"`, `{"path":"/a"}`},
		{"null error field", `name="error"`, `{"path":null,"message":"x"}`},
		{"invalid error utf8", `name="error"`, "{\"path\":\"/a\",\"message\":\"\xff\"}"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			body := "--DAYTONA-FILE-BOUNDARY\r\nContent-Disposition: form-data; " + tc.disposition +
				"\r\n\r\n" + tc.data + "\r\n--DAYTONA-FILE-BOUNDARY--\r\n"
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-download": func(w http.ResponseWriter, _ *http.Request) {
				if _, err := io.WriteString(w, body); err != nil {
					t.Error(err)
				}
			}})
			fs := newFileTestService(t, fake.Server)
			// When
			_, err := fs.ReadMany(t.Context(), []string{"/a"}, nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestReadMany_whenWholeMultipartExceedsCap(t *testing.T) {
	// Given: even an empty batch's multipart framing counts toward the limit.
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /files/bulk-download": func(w http.ResponseWriter, _ *http.Request) {
		if _, err := io.WriteString(w, "--DAYTONA-FILE-BOUNDARY--\r\n"); err != nil {
			t.Error(err)
		}
	}})
	fs := newFileTestService(t, fake.Server)
	// When
	_, err := fs.ReadMany(t.Context(), nil, &ReadFileOptions{MaxResponseBytes: 1})
	// Then
	require.ErrorIs(t, err, ErrPayloadTooLarge)
}
