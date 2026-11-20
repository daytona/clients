// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestComputerUse_whenRequiredResponseFieldMalformed(t *testing.T) {
	cases := []struct {
		name, fixture string
		call          func(context.Context, *ComputerUse) error
	}{
		{"point", `{"x":12,"y":24}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Mouse().Position(ctx)
			return err
		}},
		{"scroll", `{"success":true}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Mouse().Scroll(ctx, ComputerUseScroll{Direction: ComputerUseScrollDown}, nil)
			return err
		}},
		{"lifecycle", `{"message":"ok","status":{}}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Start(ctx, nil)
			return err
		}},
		{"restart", `{"message":"ok","status":"active","desktop":"ready"}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Restart(ctx, nil)
			return err
		}},
		{"process running", `{"processName":"wm","running":true}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Service().ProcessStatus(ctx, "wm")
			return err
		}},
		{"process restart", `{"processName":"wm","message":"ok"}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Service().Restart(ctx, "wm")
			return err
		}},
		{"process logs", `{"processName":"wm","logs":"output"}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Service().Logs(ctx, "wm")
			return err
		}},
		{"process errors", `{"processName":"wm","errors":"output"}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Service().Errors(ctx, "wm")
			return err
		}},
		{"screenshot", `{"screenshot":"AA==","sizeBytes":1}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Screen().Screenshot(ctx, nil)
			return err
		}},
		{
			"recording", `{"id":"r","fileName":"r.mp4","filePath":"/r.mp4","startTime":"2026-11-20T09:00:00Z","status":"recording"}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Recording().Get(ctx, "r"); return err },
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var object map[string]JSONValue
			require.NoError(t, json.Unmarshal([]byte(tc.fixture), &object))
			for name := range object {
				for _, replacement := range []string{"missing", "null", "[]"} {
					t.Run(name+"/"+replacement, func(t *testing.T) {
						// Given
						changed := make(map[string]JSONValue, len(object))
						for k, v := range object {
							changed[k] = v
						}
						if replacement == "missing" {
							delete(changed, name)
						} else {
							changed[name] = JSONValue(replacement)
						}
						sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, changed))
						// When
						err := tc.call(t.Context(), sandbox.ComputerUse())
						// Then
						require.ErrorIs(t, err, ErrBadRequest)
					})
				}
			}
		})
	}
}

func TestComputerUse_whenNumericOrNestedResponseMalformed(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		call       func(context.Context, *ComputerUse) error
	}{
		{"point int32", `{"x":2147483648,"y":0}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Mouse().Position(ctx)
			return err
		}},
		{"point fractional", `{"x":0.5,"y":0}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Mouse().Position(ctx)
			return err
		}},
		{"negative screenshot", `{"screenshot":"AA==","sizeBytes":-1}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Screen().Screenshot(ctx, nil)
			return err
		}},
		{"bad base64", `{"screenshot":"!","sizeBytes":1}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Screen().Screenshot(ctx, nil)
			return err
		}},
		{
			"bad cursor", `{"screenshot":"AA==","sizeBytes":1,"cursorPosition":{"x":1}}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Screen().Screenshot(ctx, nil); return err },
		},
		{"bad display list", `{"displays":[null]}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Display().Info(ctx)
			return err
		}},
		{
			"negative display", `{"displays":[{"id":0,"x":0,"y":0,"width":-1,"height":0,"isActive":true}]}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Display().Info(ctx); return err },
		},
		{
			"bad window", `{"windows":[{"id":9007199254740992,"title":"x","x":0,"y":0,"width":0,"height":0,"isActive":true}]}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Display().Windows(ctx); return err },
		},
		{"bad inventory", `{"wm":null}`, func(ctx context.Context, c *ComputerUse) error {
			_, err := c.Service().Status(ctx)
			return err
		}},
		{
			"bad process count", `{"wm":` + strings.Replace(desktopProcessFixture, `"RestartAttempts":2`, `"RestartAttempts":-1`, 1) + `}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Service().Status(ctx); return err },
		},
		{
			"bad backoff", `{"wm":` + strings.Replace(desktopProcessFixture, `"RestartBackoffMs":1250`, `"RestartBackoffMs":9007199254740992`, 1) + `}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Service().Status(ctx); return err },
		},
		{
			"bad timestamp", strings.Replace(desktopRecordingFixture, "2026-11-20T09:00:00Z", "yesterday", 1),
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Recording().Get(ctx, "r"); return err },
		},
		{
			"infinite duration", strings.Replace(desktopRecordingFixture, `"durationSeconds":1.25`, `"durationSeconds":1e309`, 1),
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Recording().Get(ctx, "r"); return err },
		},
		{
			"bad recording list", `{"recordings":[{}]}`,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Recording().List(ctx); return err },
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, err := w.Write([]byte(tc.body))
				if err != nil {
					t.Errorf("write desktop response: %v", err)
				}
			})
			// When
			err := tc.call(t.Context(), sandbox.ComputerUse())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}
