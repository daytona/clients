// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build linux

package daytona

import (
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestMultiNodeUploadRejectsFifoWithoutWriter(t *testing.T) {
	fifo := filepath.Join(t.TempDir(), "pipe")
	require.NoError(t, syscall.Mkfifo(fifo, 0600))
	nodes := &SandboxNodes{members: []*SandboxNode{{}, {}}}
	done := make(chan error, 1)
	go func() { done <- nodes.UploadFile(t.Context(), fifo, "/file") }()
	select {
	case err := <-done:
		require.ErrorContains(t, err, "seekable")
	case <-time.After(3 * time.Second):
		t.Fatal("opening FIFO waited for a writer")
	}
}
