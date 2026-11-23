// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"math"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestImagesBuildFromContextReader_whenSizeIsNotPositive(t *testing.T) {
	for _, size := range []int64{0, -1, -5, math.MinInt64} {
		t.Run(strconv.FormatInt(size, 10), func(t *testing.T) {
			// Given a caller-owned reader with a non-positive declared size.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			reader := bytes.NewReader([]byte("archive"))

			// When submitting the archive.
			_, err := client.Images().BuildFromContextReader(t.Context(), reader, size, nil)

			// Then the error explains the lower bound, not the upper bound.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, "build context size must be positive", detail.Message)
			require.Equal(t, "SDK", detail.Source)
			require.Empty(t, fake.requests())
		})
	}
}
