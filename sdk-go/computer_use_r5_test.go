// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestComputerUseReturnsNil_whenResponseMalformed(t *testing.T) {
	cases := []struct {
		name, body string
		call       func(*testing.T, *ComputerUse) (bool, error)
	}{
		{"position", `{"x":"bad","y":3}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Mouse().Position(t.Context())
			return got == nil, err
		}},
		{"move", `{"x":"bad","y":3}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Mouse().Move(t.Context(), ComputerUsePoint{})
			return got == nil, err
		}},
		{"click", `{"x":"bad","y":3}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Mouse().Click(t.Context(), ComputerUsePoint{}, nil)
			return got == nil, err
		}},
		{"drag", `{"x":"bad","y":3}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Mouse().Drag(t.Context(), ComputerUseDrag{}, nil)
			return got == nil, err
		}},
		{"scroll", `{"success":"bad"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Mouse().Scroll(t.Context(), ComputerUseScroll{}, nil)
			return got == nil, err
		}},
		{"screenshot base64", `{"screenshot":"!","sizeBytes":1}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Screen().Screenshot(t.Context(), nil)
			return got == nil, err
		}},
		{"screenshot cursor", `{"screenshot":"AA==","sizeBytes":1,"cursorPosition":{"x":1}}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Screen().Screenshot(t.Context(), nil)
			return got == nil, err
		}},
		{"displays", `{"displays":[{"id":1}]}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Displays().Info(t.Context())
			return got == nil, err
		}},
		{"display envelope", `{}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Displays().Info(t.Context())
			return got == nil, err
		}},
		{"windows", `{"windows":[{"id":1}]}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Displays().Windows(t.Context())
			return got == nil, err
		}},
		{"window envelope", `{}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Displays().Windows(t.Context())
			return got == nil, err
		}},
		{"process status", `{"processName":"wm","running":"bad"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Addons().ProcessStatus(t.Context(), "wm")
			return got == nil, err
		}},
		{"process restart", `{"processName":"wm","message":false}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Addons().Restart(t.Context(), "wm")
			return got == nil, err
		}},
		{"process logs", `{"processName":"wm","logs":false}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Addons().Logs(t.Context(), "wm")
			return got == nil, err
		}},
		{"process errors", `{"processName":"wm","errors":false}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Addons().Errors(t.Context(), "wm")
			return got == nil, err
		}},
		{"addon map", `{"wm":{"Running":true}}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Addons().Status(t.Context())
			return got == nil, err
		}},
		{"start", `{"message":"started","status":{"wm":{"Running":true}}}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Start(t.Context(), nil)
			return got == nil, err
		}},
		{"stop", `{"message":false,"status":{}}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Stop(t.Context())
			return got == nil, err
		}},
		{"restart", `{"message":"restarted","status":"active","desktop":"failed"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Restart(t.Context(), nil)
			return got == nil, err
		}},
		{"status", `{"status":"inactive","desktop":"failed"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Status(t.Context())
			return got == nil, err
		}},
		{"status cause", `{"status":"inactive","desktop":"failed","cause":{"code":"start_failed","message":false}}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Status(t.Context())
			return got == nil, err
		}},
		{"recording start", `{"id":"rec"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Recordings().Start(t.Context(), nil)
			return got == nil, err
		}},
		{"recording stop", `{"id":"rec"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Recordings().Stop(t.Context(), "rec")
			return got == nil, err
		}},
		{"recording get", `{"id":"rec"}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Recordings().Get(t.Context(), "rec")
			return got == nil, err
		}},
		{"recording list", `{"recordings":[` + desktopRecordingFixture + `,{"id":"rec"}]}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Recordings().List(t.Context())
			return got == nil, err
		}},
		{"recording envelope", `{}`, func(t *testing.T, c *ComputerUse) (bool, error) {
			got, err := c.Recordings().List(t.Context())
			return got == nil, err
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Given
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(tc.body)))
			// When
			isNil, err := tc.call(t, sandbox.ComputerUse())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.True(t, isNil, "malformed response must not return a partial value")
		})
	}
}

func TestComputerUseValidationNamesGoOptions_whenInputInvalid(t *testing.T) {
	cases := []struct {
		option string
		call   func(*testing.T, *ComputerUse) error
	}{
		{"Delay", func(t *testing.T, c *ComputerUse) error {
			return c.Keyboard().Type(t.Context(), "x", &ComputerUseTypeOptions{Delay: -time.Second})
		}},
		{"MaxResponseBytes", func(t *testing.T, c *ComputerUse) error {
			_, err := c.Recordings().Download(t.Context(), "rec", &ComputerUseRecordingDownloadOptions{MaxResponseBytes: -2})
			return err
		}},
		{"MaxDepth", func(t *testing.T, c *ComputerUse) error {
			depth := -1
			_, err := c.A11y().Tree(t.Context(), ComputerUseA11yScopeAll, &ComputerUseA11yTreeOptions{MaxDepth: &depth})
			return err
		}},
	}
	for _, tc := range cases {
		t.Run(tc.option, func(t *testing.T) {
			// Given
			sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, JSONValue(`{}`)))
			// When
			err := tc.call(t, sandbox.ComputerUse())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, tc.option)
			require.Len(t, fake.requests(), 1)
		})
	}
}
