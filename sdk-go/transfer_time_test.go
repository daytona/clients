// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

// A wire-level response body driven by synctest's clock, not real sleeps.
type fileTimedBody struct {
	ctx    context.Context
	gap    time.Duration
	chunks int
}

func (b *fileTimedBody) Read(p []byte) (int, error) {
	if b.chunks == 0 {
		return 0, io.EOF
	}
	timer := time.NewTimer(b.gap)
	defer timer.Stop()
	select {
	case <-b.ctx.Done():
		return 0, b.ctx.Err()
	case <-timer.C:
		p[0] = 'x'
		b.chunks--
		return 1, nil
	}
}

func (*fileTimedBody) Close() error { return nil }

type fileTimeTransport struct{ gap time.Duration }

func (t fileTimeTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	return &http.Response{
		StatusCode: http.StatusOK, Header: make(http.Header), ContentLength: 3, Request: r,
		Body: &fileTimedBody{ctx: r.Context(), gap: t.gap, chunks: 3},
	}, nil
}

func TestFileTransfer_whenMoving_outlastsTotalTimeout(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: each gap is below 1s but the complete transfer takes 2.25s.
		fs := newFileTestService(t, &httptest.Server{URL: "https://example.invalid"},
			WithRequestTimeout(time.Second), WithHTTPClient(&http.Client{Transport: fileTimeTransport{gap: 750 * time.Millisecond}}))
		// When
		data, err := fs.ReadFile(t.Context(), "/a", nil)
		// Then
		require.NoError(t, err)
		require.Equal(t, []byte("xxx"), data)
	})
}

func TestFileTransfer_whenNoTimeout_disablesIdleBound(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given: every gap exceeds the configured client timeout.
		fs := newFileTestService(t, &httptest.Server{URL: "https://example.invalid"},
			WithRequestTimeout(time.Second), WithHTTPClient(&http.Client{Transport: fileTimeTransport{gap: 2 * time.Second}}))
		// When
		data, err := fs.ReadFile(t.Context(), "/a", &ReadFileOptions{Timeout: NoTimeout})
		// Then
		require.NoError(t, err)
		require.Equal(t, []byte("xxx"), data)
	})
}
