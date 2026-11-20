// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestUploadFile_whenLocalSourceMissing_reportsCause(t *testing.T) {
	// Given
	sandbox, fake := processTestSandbox(t, nil)
	path := filepath.Join(t.TempDir(), "missing")
	_, cause := os.Stat(path)
	require.Error(t, cause)

	// When
	err := sandbox.FS().UploadFile(t.Context(), path, "/file", nil)

	// Then
	require.ErrorIs(t, err, os.ErrNotExist)
	require.ErrorContains(t, err, cause.Error())
	require.Empty(t, fake.requests())
}

func TestExec_whenOutputWriterFails_reportsCause(t *testing.T) {
	// Given
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes": jsonHandler(t, processFixture("p")),
		"GET /processes/p/logs": processSSEHandler(t,
			"event: log\ndata: {\"cursor\":\"1\",\"channel\":\"stdout\",\"data\":\"x\",\"timestamp\":1}\n\n"),
	})

	// When
	_, err := sandbox.Exec(t.Context(), "command", &ExecOptions{Stdout: downloadFailingWriter{syscall.EPIPE}})

	// Then
	require.ErrorIs(t, err, syscall.EPIPE)
	require.ErrorContains(t, err, syscall.EPIPE.Error())
}

func TestTransportError_whenCauseAlreadyIsMessage_reportsItOnce(t *testing.T) {
	// Given
	cause := syscall.ECONNRESET
	// When
	err := transportError(t.Context(), cause)
	// Then
	require.ErrorIs(t, err, cause)
	require.Equal(t, 1, strings.Count(err.Error(), cause.Error()))
}

func TestWrapError_whenCauseIsAbsent_preservesExplanation(t *testing.T) {
	// Given / When
	err := wrapError(nil, "local failure")
	// Then
	require.Equal(t, "local failure", err.Message)
	require.NoError(t, err.Unwrap())
}
