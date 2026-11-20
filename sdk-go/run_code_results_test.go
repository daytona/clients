// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestRunCode_whenRichOutputs(t *testing.T) {
	// Given
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/wait"] = jsonHandler(t, map[string]any{"reason": "future_reason"})
	routes["GET /processes/p/logs"] = jsonHandler(t, map[string]any{
		"frames": []any{
			map[string]any{"cursor": "1", "timestamp": 1, "channel": "setup", "data": "installing"},
			map[string]any{"cursor": "2", "timestamp": 2, "channel": "stdout", "data": "a"},
			map[string]any{"cursor": "3", "timestamp": 3, "channel": "stderr", "data": "b"},
			map[string]any{"cursor": "4", "timestamp": 4, "channel": "stdout", "data": "c"},
			map[string]any{"cursor": "5", "timestamp": 5, "channel": "display", "data": `{"mime":"text/html","data":"<b>hi</b>"}`},
			map[string]any{"cursor": "6", "timestamp": 6, "channel": "execute_result", "data": `{"data":{"text/plain":"42","image/png":"abc","image/svg+xml":"<svg/>","application/json":{"value":42},"future/mime":[1]}}`},
			map[string]any{"cursor": "7", "timestamp": 7, "channel": "error", "data": `{"ename":"First","evalue":"ignored"}`},
			map[string]any{"cursor": "8", "timestamp": 8, "channel": "error", "data": `{"ename":"Last","evalue":"oops","traceback":["line",3,null]}`},
			map[string]any{"cursor": "9", "timestamp": 9, "channel": "future_channel", "data": "ignored"},
		}, "nextCursor": "9", "eof": true, "truncatedHead": false,
	})
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	// When
	result, err := sandbox.RunCode(t.Context(), "answer()", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, InterpreterLogs{Stdout: "ac", Stderr: "b"}, result.Logs)
	require.Equal(t, ProcessReasonUnknown, result.Process.Reason)
	require.Equal(t, "future_reason", result.Process.RawReason)
	require.Nil(t, result.Process.ExitCode)
	require.Len(t, result.Results, 2)
	require.False(t, result.Results[0].IsMainResult)
	require.Equal(t, "<b>hi</b>", *result.Results[0].HTML)
	require.True(t, result.Results[1].IsMainResult)
	require.Equal(t, "42", *result.Results[1].Text)
	require.Equal(t, "abc", *result.Results[1].PNG)
	require.Equal(t, "<svg/>", *result.Results[1].SVG)
	require.JSONEq(t, `{"value":42}`, string(result.Results[1].JSON))
	require.JSONEq(t, `[1]`, string(result.Results[1].Data["future/mime"]))
	require.Equal(t, &InterpreterError{Name: "Last", Value: "oops", Traceback: []string{"line", "3", "null"}},
		result.Error)
}

func TestRunCode_whenOutputPayloadVaries(t *testing.T) {
	tests := []struct {
		payload string
		count   int
	}{
		{`not-json`, 0},
		{`[]`, 0},
		{`null`, 0},
		{`5`, 0},
		{`{"data":5}`, 0},
		{`{"data":null}`, 0},
		{`{"data":[]}`, 0},
		{`{"data":5,"mime":3}`, 0},
		{`{"data":null,"mime":"application/json"}`, 1},
		{`{ }`, 1},
		{`{"text/plain":"direct"}`, 1},
		{`{"data":{"text/plain":5}}`, 1},
		{`{"mime":"","data":""}`, 1},
		{`{"data":{"x":NaN}}`, 0},
	}
	for _, test := range tests {
		t.Run(test.payload, func(t *testing.T) {
			// Given
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/logs"] = jsonHandler(t, map[string]any{
				"frames": []any{map[string]any{
					"cursor": "1", "timestamp": 1, "channel": "display", "data": test.payload,
				}}, "nextCursor": "1", "eof": true, "truncatedHead": false,
			})
			fake := newFakeServer(t, routes)
			sandbox := interpreterTestSandbox(t, fake)
			// When
			result, err := sandbox.RunCode(t.Context(), "1", nil)
			// Then
			require.NoError(t, err)
			require.Len(t, result.Results, test.count)
		})
	}
}

func TestRunCode_whenNestedFrameExceedsLimit(t *testing.T) {
	for _, channel := range []string{"display", "error"} {
		t.Run(channel, func(t *testing.T) {
			// Given
			routes := interpreterRunRoutes(t)
			routes["GET /processes/p/logs"] = jsonHandler(t, map[string]any{
				"frames": []any{map[string]any{
					"cursor": "1", "timestamp": 1, "channel": channel,
					"data": `{"data":` + strings.Repeat("[", 600) + "0" + strings.Repeat("]", 600) + "}",
				}}, "nextCursor": "1", "eof": true, "truncatedHead": false,
			})
			fake := newFakeServer(t, routes)
			sandbox := interpreterTestSandbox(t, fake)
			// When
			_, err := sandbox.RunCode(t.Context(), "1", nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestInterpreterError_whenCoercingJSONValues(t *testing.T) {
	// Given
	raw := json.RawMessage(`{"ename":false,"evalue":{"x":1},"traceback":[["a",null,true],{},null]}`)
	// When
	result, valid, err := parseInterpreterError(string(raw))
	// Then
	require.NoError(t, err)
	require.True(t, valid)
	require.Equal(t, InterpreterError{
		Name: "false", Value: "[object Object]", Traceback: []string{"a,,true", "[object Object]", "null"},
	}, result)
}

func TestInterpreterError_whenCoercingJSONNumbers(t *testing.T) {
	tests := []struct {
		raw  string
		want string
	}{
		{"0", "0"},
		{"-0", "0"},
		{"1e21", "1e+21"},
		{"1e-7", "1e-7"},
		{"1e6", "1000000"},
		{"1e-6", "0.000001"},
		{"1e400", "Infinity"},
		{"-1e400", "-Infinity"},
	}
	for _, test := range tests {
		t.Run(test.raw, func(t *testing.T) {
			// Given
			payload := `{"ename":` + test.raw + `}`
			// When
			result, valid, err := parseInterpreterError(payload)
			// Then
			require.NoError(t, err)
			require.True(t, valid)
			require.Equal(t, test.want, result.Name)
		})
	}
}
