// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

// zeroProgressReader models a legal reader that makes no progress before
// returning; time runs on synctest's virtual clock.
type zeroProgressReader struct{ remaining int }

func (r *zeroProgressReader) Read([]byte) (int, error) {
	if r.remaining == 0 {
		return 0, io.EOF
	}
	r.remaining--
	<-time.After(6 * time.Second)
	return 0, nil
}

func TestTransfer_whenReadsReturnZeroProgress(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		owner := &Client{ctx: t.Context()}
		_, scope := newRequestScope(t.Context(), owner, 10*time.Second)
		body := &responseBody{ReadCloser: io.NopCloser(&zeroProgressReader{remaining: 3}), scope: scope, idle: true}
		defer func() { require.NoError(t, body.Close()) }()
		// When
		started := time.Now()
		_, err := io.ReadAll(body)
		// Then
		require.ErrorIs(t, err, ErrClientTimeout)
		require.ErrorIs(t, err, context.DeadlineExceeded)
		require.LessOrEqual(t, time.Since(started), 12*time.Second)
	})
}
