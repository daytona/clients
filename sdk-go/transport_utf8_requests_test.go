// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenJSONBodyContainsInvalidUTF8_rejectsBeforeRequest(t *testing.T) {
	for _, test := range []struct {
		name    string
		options CreateOptions
		path    string
	}{
		{"env key", CreateOptions{Env: map[string]string{"K\xff": "value"}}, `env["K\xff"]`},
		{"env value", CreateOptions{Env: map[string]string{"KEY": "\xff"}}, `env["KEY"]`},
		{"volume id", CreateOptions{Volumes: []VolumeMount{{VolumeID: "vol\xff", MountPath: "/mnt"}}}, "volumes[0].volume"},
		{"mount path", CreateOptions{Volumes: []VolumeMount{{VolumeID: "vol", MountPath: "/mnt/\xff"}}}, "volumes[0].mount_path"},
		{"subpath", CreateOptions{Volumes: []VolumeMount{{VolumeID: "vol", MountPath: "/mnt", Subpath: "\xff"}}}, "volumes[0].subpath"},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			test.options.Wait = NoWait
			// When
			_, err := client.Create(t.Context(), "", &test.options)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Contains(t, detail.Message, test.path)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateMacOS_whenSecretNameContainsInvalidUTF8_rejectsBeforeRequest(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.CreateMacOS(t.Context(), "sonoma", &CreateMacOSOptions{Wait: NoWait, Secrets: []string{"secret\xff"}})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, SourceSDK, detail.Source)
	require.Contains(t, detail.Message, "secrets[0]")
	require.Empty(t, fake.requests())
}

func TestKeyboardType_whenTextContainsInvalidUTF8_rejectsBeforeRequest(t *testing.T) {
	// Given: constructing the handle requires no HTTP, including authentication.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	sandbox, setupErr := client.newSandbox(sandboxInit{
		Info: SandboxInfo{ID: "sbx"}, Options: GetSandboxOptions{ToolboxURL: fake.Server.URL},
	})
	require.NoError(t, setupErr)
	// When
	err := sandbox.ComputerUse().Keyboard().Type(t.Context(), "text\xff", nil)
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	var detail *Error
	require.ErrorAs(t, err, &detail)
	require.Equal(t, SourceSDK, detail.Source)
	require.Contains(t, detail.Message, "text")
	require.Empty(t, fake.requests())
}

func TestTransport_whenInvalidBodyNeedsBearer_rejectsBeforeAuthentication(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	wire := transport{owner: client, baseURL: fake.Server.URL, auth: invalidBodyCredential{t}}
	// When
	err := wire.do(t.Context(), apiRequest{Path: "/echo", Body: map[string]string{"key": "\xff"}})
	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

type invalidBodyCredential struct{ t *testing.T }

func (c invalidBodyCredential) bearer(context.Context) (string, error) {
	c.t.Error("invalid JSON body must be rejected before authentication")
	return "", nil
}

func (c invalidBodyCredential) unauthorized(context.Context, string) (string, error) {
	c.t.Error("invalid JSON body must not refresh authentication")
	return "", nil
}
