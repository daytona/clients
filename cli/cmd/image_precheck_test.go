// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"archive/tar"
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestImageBuild_rejectsMissingDockerfile_whenContextIsPlainTar(t *testing.T) {
	// Given a valid archive containing a different file.
	var buffer bytes.Buffer
	archive := tar.NewWriter(&buffer)
	require.NoError(t, archive.WriteHeader(&tar.Header{Name: "app.txt", Mode: 0o600, Size: 3}))
	_, err := archive.Write([]byte("app"))
	require.NoError(t, err)
	require.NoError(t, archive.Close())
	context := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(context, buffer.Bytes(), 0o600))
	// When building through the command.
	_, _, err = runCLI(t, []string{"image", "build", "bad", "--context", context}, nil)
	// Then the missing Dockerfile is detected before any request.
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "Dockerfile")
}

func TestCheckImageDockerfile_acceptsFile_whenPlainTarContainsIt(t *testing.T) {
	// Given a plain tar with a normalized path to the selected Dockerfile.
	var buffer bytes.Buffer
	archive := tar.NewWriter(&buffer)
	require.NoError(t, archive.WriteHeader(&tar.Header{Name: "./build/Dockerfile", Mode: 0o600, Size: 0}))
	require.NoError(t, archive.Close())
	context := filepath.Join(t.TempDir(), "context.tar")
	require.NoError(t, os.WriteFile(context, buffer.Bytes(), 0o600))
	// When checking the selected Dockerfile.
	err := checkImageDockerfile(context, "build/Dockerfile")
	// Then the archive can proceed to upload.
	require.NoError(t, err)
}
