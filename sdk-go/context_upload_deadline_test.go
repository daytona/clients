// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestContextUploadReportsCallerDeadlineOnLastAttempt(t *testing.T) {
	// Given two retryable failures followed by a caller deadline, with no idle limit.
	synctest.Test(t, func(t *testing.T) {
		attempts := 0
		transport := contextTestTransport(func(request *http.Request) (*http.Response, error) {
			attempts++
			if attempts < contextUploadAttempts {
				return nil, errors.New("connection failed")
			}
			<-request.Context().Done()
			return nil, request.Context().Err()
		})
		client, err := New(WithAPIKey("key"), WithRequestTimeout(NoTimeout),
			WithHTTPClient(&http.Client{Transport: transport}))
		require.NoError(t, err)
		defer func() { require.NoError(t, client.Close()) }()
		uploader := contextTestUploader(t, client, []byte("abc"))
		ctx, cancel := context.WithTimeout(t.Context(), time.Second)
		defer cancel()
		// When
		err = uploader.putPart(ctx, contextUploadPart{number: 1, bytes: 3, url: "https://storage.invalid/part"})
		// Then the caller's deadline is not mislabeled as a storage idle timeout.
		require.ErrorIs(t, err, context.DeadlineExceeded)
		require.ErrorIs(t, err, ErrClientTimeout)
		require.NotContains(t, err.Error(), "made no progress")
		require.Equal(t, contextUploadAttempts, attempts)
	})
}
