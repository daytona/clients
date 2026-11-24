// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"archive/tar"
	"bytes"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestImagesBuildContextReader(t *testing.T) {
	// Given an archive assembled in memory, with no caller-owned context path.
	client := newBuildClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-e2e-reader")
	cleanupImage(t, client, name)
	var archive bytes.Buffer
	writer := tar.NewWriter(&archive)
	dockerfile := []byte("FROM " + smokeImage() + "\nRUN printf 'reader context\\n' > /message.txt\n")
	require.NoError(t, writer.WriteHeader(&tar.Header{Name: "Dockerfile", Mode: 0o644, Size: int64(len(dockerfile))}))
	_, err := writer.Write(dockerfile)
	require.NoError(t, err)
	require.NoError(t, writer.Close())

	// When submitting the archive bytes and waiting for readiness.
	image, err := client.Images().BuildFromContextReader(ctx, bytes.NewReader(archive.Bytes()), int64(archive.Len()),
		&daytona.BuildFromContextOptions{
			BuildImageOptions: daytona.BuildImageOptions{Name: name}, Dockerfile: "./Dockerfile",
		})
	require.NoError(t, err)
	ready, err := client.Images().Get(ctx, image.ID, &daytona.GetArtifactOptions{Wait: true, Timeout: 4 * time.Minute})

	// Then the stored archive is usable by the real build service.
	require.NoError(t, err)
	require.Equal(t, daytona.ImageStatusReady, ready.Status, ready.Error)
}

func TestImagesBuildContextReaderRejectsShortArchive(t *testing.T) {
	// Given a declared size larger than the supplied bytes.
	client := newClient(t)
	// When
	_, err := client.Images().BuildFromContextReader(testContext(t), bytes.NewReader([]byte("short")), 6, nil)
	// Then no build is submitted, and callers can identify invalid input.
	require.ErrorIs(t, err, daytona.ErrBadRequest)
}
