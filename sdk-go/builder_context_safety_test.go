// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImageBuildRejectsLinkThroughIgnoredEscapingParent(t *testing.T) {
	// Given a builder directory with a kept link routed outside via an ignored parent.
	root, outside := t.TempDir(), t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(outside, "private"), []byte("private"), 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(root, ".dockerignore"), []byte("hidden\n"), 0o600))
	require.NoError(t, os.Symlink(outside, filepath.Join(root, "hidden")))
	require.NoError(t, os.Symlink("hidden/private", filepath.Join(root, "link")))
	builder := NewImageBuilder("alpine:3.22").AddLocalDir(root, "/app")
	server := newFakeServer(t, nil)
	client := newTestClient(t, server.Server)

	// When building through the public API.
	_, err := client.Images().Build(t.Context(), builder, nil)

	// Then unsafe input is refused locally without submitting or uploading it.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, server.requests())
}
