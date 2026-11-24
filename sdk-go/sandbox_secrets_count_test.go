// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxSecretsCaptureCountInt32Bounds(t *testing.T) {
	for _, count := range []int64{math.MinInt32 - 1, math.MinInt32, 0, math.MaxInt32, math.MaxInt32 + 1} {
		t.Run(strconv.FormatInt(count, 10), func(t *testing.T) {
			// Given
			body := sandboxSecretsFixture()
			body["capture_unresolved_count"] = count
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/secrets": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			info, err := sandbox.GetSecrets(t.Context())
			// Then
			if count < math.MinInt32 || count > math.MaxInt32 {
				require.ErrorIs(t, err, ErrBadRequest)
				require.Contains(t, err.Error(), "capture_unresolved_count")
				return
			}
			require.NoError(t, err)
			require.NotNil(t, info.CaptureUnresolvedCount)
			require.Equal(t, int32(count), *info.CaptureUnresolvedCount)
		})
	}
}

func TestSandboxSecretsCaptureCountAbsent(t *testing.T) {
	for _, present := range []bool{false, true} {
		t.Run(strconv.FormatBool(present), func(t *testing.T) {
			// Given
			body := sandboxSecretsFixture()
			delete(body, "capture_unresolved_count")
			if present {
				body["capture_unresolved_count"] = nil
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /sandboxes/sbx/secrets": policyResponse(t, body),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			info, err := sandbox.GetSecrets(t.Context())
			// Then
			require.NoError(t, err)
			require.Nil(t, info.CaptureUnresolvedCount)
		})
	}
}
