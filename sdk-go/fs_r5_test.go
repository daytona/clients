// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFileLists_whenRequiredListMalformed_namesField(t *testing.T) {
	cases := []struct {
		name, route, field string
		payload            map[string]json.RawMessage
		call               func(context.Context, *FileSystem) error
	}{
		{"list", "GET /files", "items", nil, func(ctx context.Context, fs *FileSystem) error {
			_, err := fs.List(ctx, "/", nil)
			return err
		}},
		{"grep", "GET /files/find", "items", nil, func(ctx context.Context, fs *FileSystem) error {
			_, err := fs.Grep(ctx, "/", "needle")
			return err
		}},
		{"replace", "POST /files/replace", "items", nil, func(ctx context.Context, fs *FileSystem) error {
			_, err := fs.Replace(ctx, []string{"/a"}, "old", "new")
			return err
		}},
		{"glob", "GET /files/search", "files", nil, func(ctx context.Context, fs *FileSystem) error {
			_, err := fs.Glob(ctx, "/", "*")
			return err
		}},
		{
			"bulk files", "POST /files/bulk-upload", "files",
			map[string]json.RawMessage{"errors": json.RawMessage(`[]`)},
			func(ctx context.Context, fs *FileSystem) error {
				_, err := fs.WriteMany(ctx, map[string][]byte{"/a": {}}, nil)
				return err
			},
		},
		{
			"bulk errors", "POST /files/bulk-upload", "errors",
			map[string]json.RawMessage{"files": json.RawMessage(`[]`)},
			func(ctx context.Context, fs *FileSystem) error {
				_, err := fs.WriteMany(ctx, map[string][]byte{"/a": {}}, nil)
				return err
			},
		},
	}
	for _, tc := range cases {
		for _, shape := range []string{"missing", "null", "object"} {
			t.Run(tc.name+"/"+shape, func(t *testing.T) {
				// Given a malformed required list and otherwise valid sibling fields.
				payload := make(map[string]json.RawMessage)
				for key, value := range tc.payload {
					payload[key] = value
				}
				switch shape {
				case "null":
					payload[tc.field] = nil
				case "object":
					payload[tc.field] = json.RawMessage(`{}`)
				}
				fake := newFakeServer(t, map[string]http.HandlerFunc{tc.route: jsonHandler(t, payload)})
				fs := newFileTestService(t, fake.Server)
				// When the response is parsed.
				err := tc.call(t.Context(), fs)
				// Then the diagnostic names the broken wire field.
				require.ErrorIs(t, err, ErrBadRequest)
				require.ErrorContains(t, err, "response field "+tc.field)
			})
		}
	}
}
