// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"net/url"
	"testing"

	"github.com/stretchr/testify/require"
)

func computerUseA11yNodeFixture(id string) map[string]any {
	return map[string]any{
		"id": id, "role": "future-role", "name": "Control",
		"bounds": map[string]any{"x": -2147483648, "y": 2147483647, "width": 10, "height": -1},
		"states": []string{"future-state"},
	}
}

func computerUseA11yTestSandbox(t *testing.T, handler http.HandlerFunc) (*Sandbox, *fakeServer) {
	t.Helper()
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /sandboxes/sbx": jsonHandler(t, sandboxFixture("sbx")),
		"/computeruse/":      handler,
	})
	client := newTestClient(t, fake.Server)
	sandbox, err := client.Get(t.Context(), "sbx", &GetSandboxOptions{
		ToolboxURL:  fake.Server.URL,
		AccessToken: "test-" + "sandbox-token",
	})
	require.NoError(t, err)
	return sandbox, fake
}

func TestComputerUseA11yTreeMapsOrderedNodes(t *testing.T) {
	// Given
	root := computerUseA11yNodeFixture("root")
	cut := computerUseA11yNodeFixture("cut")
	cut["value"], cut["valueTruncated"], cut["description"] = "abcd", true, ""
	cut["actions"] = []string{"press", "future-action"}
	plain := computerUseA11yNodeFixture("plain")
	plain["children"] = []any{computerUseA11yNodeFixture("leaf")}
	root["children"] = []any{cut, plain}
	sandbox, fake := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{"root": root, "truncated": true}))
	pid, depth := 77, 2

	// When
	tree, err := sandbox.ComputerUse().A11y().Tree(t.Context(), ComputerUseA11yScopePID, &ComputerUseA11yTreeOptions{PID: &pid, MaxDepth: &depth})

	// Then
	require.NoError(t, err)
	require.True(t, tree.Truncated)
	require.Equal(t, int32(-2147483648), tree.Root.Bounds.X)
	require.Equal(t, int32(2147483647), tree.Root.Bounds.Y)
	require.Equal(t, int32(-1), tree.Root.Bounds.Height)
	require.Equal(t, "future-role", tree.Root.Role)
	require.Equal(t, []string{"future-state"}, tree.Root.States)
	require.Equal(t, "cut", tree.Root.Children[0].ID)
	require.Equal(t, "abcd", *tree.Root.Children[0].Value)
	require.Empty(t, *tree.Root.Children[0].Description)
	require.True(t, tree.Root.Children[0].ValueTruncated)
	require.Equal(t, []string{"press", "future-action"}, tree.Root.Children[0].Actions)
	require.Equal(t, "plain", tree.Root.Children[1].ID)
	require.False(t, tree.Root.Children[1].ValueTruncated)
	require.Equal(t, "leaf", tree.Root.Children[1].Children[0].ID)
	require.False(t, tree.Root.Children[1].Children[0].ValueTruncated)
	requests := fake.requests()
	request := requests[len(requests)-1]
	require.Equal(t, http.MethodGet, request.Method)
	require.Equal(t, "/computeruse/a11y/tree", request.Path)
	require.Equal(t, url.Values{"scope": {"pid"}, "pid": {"77"}, "maxDepth": {"2"}}, request.Query)
}

func TestComputerUseA11yFindSendsFilters(t *testing.T) {
	// Given
	first, second := computerUseA11yNodeFixture("first"), computerUseA11yNodeFixture("second")
	first["valueTruncated"] = true
	second["description"], second["value"], second["actions"], second["children"], second["valueTruncated"] = nil, nil, nil, nil, nil
	sandbox, fake := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{"matches": []any{first, second}, "truncated": true}))
	pid, limit, role, name := 0, 0, "button", ""

	// When
	found, err := sandbox.ComputerUse().A11y().Find(t.Context(), ComputerUseA11yScopeAll, &ComputerUseA11yFindOptions{
		PID: &pid, Role: &role, Name: &name, NameMatch: ComputerUseA11yNameMatchSubstring,
		States: []string{}, Limit: &limit,
	})

	// Then
	require.NoError(t, err)
	require.True(t, found.Truncated)
	require.Equal(t, "first", found.Matches[0].ID)
	require.True(t, found.Matches[0].ValueTruncated)
	require.Equal(t, "second", found.Matches[1].ID)
	require.False(t, found.Matches[1].ValueTruncated)
	require.Nil(t, found.Matches[1].Description)
	require.Nil(t, found.Matches[1].Value)
	require.Nil(t, found.Matches[1].Actions)
	require.Nil(t, found.Matches[1].Children)
	requests := fake.requests()
	request := requests[len(requests)-1]
	require.Equal(t, http.MethodPost, request.Method)
	require.Equal(t, "/computeruse/a11y/find", request.Path)
	require.JSONEq(t, `{"scope":"all","pid":0,"role":"button","name":"","nameMatch":"substring","states":[],"limit":0}`, string(request.Body))
}

func TestComputerUseA11yFindOmitsDefaultFilters(t *testing.T) {
	// Given
	sandbox, fake := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{"matches": []any{}, "truncated": false}))

	// When
	found, err := sandbox.ComputerUse().A11y().Find(t.Context(), ComputerUseA11yScopeAll, nil)

	// Then
	require.NoError(t, err)
	require.NotNil(t, found.Matches)
	require.Empty(t, found.Matches)
	requests := fake.requests()
	require.JSONEq(t, `{"scope":"all"}`, string(requests[len(requests)-1].Body))
}

func TestComputerUseA11yActionsSendExactBodies(t *testing.T) {
	action := ""
	for _, test := range []struct {
		name string
		path string
		body string
		call func(context.Context, *ComputerUseA11y) error
	}{
		{"focus", "focus", `{"id":"../node"}`, func(ctx context.Context, a *ComputerUseA11y) error { return a.Focus(ctx, "../node") }},
		{"invoke default", "invoke", `{"id":"n"}`, func(ctx context.Context, a *ComputerUseA11y) error { return a.Invoke(ctx, "n", nil) }},
		{"invoke explicit empty", "invoke", `{"id":"n","action":""}`, func(ctx context.Context, a *ComputerUseA11y) error {
			return a.Invoke(ctx, "n", &ComputerUseA11yInvokeOptions{Action: &action})
		}},
		{"set empty value", "value", `{"id":"n","value":""}`, func(ctx context.Context, a *ComputerUseA11y) error { return a.SetValue(ctx, "n", "") }},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			sandbox, fake := computerUseA11yTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })

			// When
			err := test.call(t.Context(), sandbox.ComputerUse().A11y())

			// Then
			require.NoError(t, err)
			requests := fake.requests()
			request := requests[len(requests)-1]
			require.Equal(t, http.MethodPost, request.Method)
			require.Equal(t, "/computeruse/a11y/node/"+test.path, request.Path)
			require.JSONEq(t, test.body, string(request.Body))
		})
	}
}

func TestComputerUseA11yPropagatesServerErrors(t *testing.T) {
	// Given
	sandbox, _ := computerUseA11yTestSandbox(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		writeJSON(t, w, map[string]any{"code": "DESKTOP_NOT_READY", "source": "TOOLBOX", "message": "not ready"})
	})

	// When
	err := sandbox.ComputerUse().A11y().Focus(t.Context(), "n")

	// Then
	require.ErrorIs(t, err, ErrDesktopNotReady)
	var sdkErr *Error
	require.ErrorAs(t, err, &sdkErr)
	require.Equal(t, "TOOLBOX", sdkErr.Source)
}

func TestComputerUseA11yHonorsCancellation(t *testing.T) {
	// Given
	sandbox, fake := computerUseA11yTestSandbox(t, jsonHandler(t, map[string]any{}))
	before := len(fake.requests())
	ctx, cancel := context.WithCancel(t.Context())
	cancel()

	// When
	err := sandbox.ComputerUse().A11y().SetValue(ctx, "n", "v")

	// Then
	require.ErrorIs(t, err, context.Canceled)
	require.Len(t, fake.requests(), before)
}
