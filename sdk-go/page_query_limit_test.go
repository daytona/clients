// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPageQuery_whenLimitIsZero_usesServerDefault(t *testing.T) {
	// When
	params, err := pageQuery("", 0)
	// Then: zero is the server default, so no limit is sent
	require.NoError(t, err)
	require.Empty(t, params.Get("limit"))
}

func TestPageQuery_whenLimitIsOutOfRange_namesTheDefault(t *testing.T) {
	for _, limit := range []int{-1, 201} {
		// When
		_, err := pageQuery("", limit)
		// Then the message names the zero default as well as the range
		require.ErrorIs(t, err, ErrBadRequest)
		require.Contains(t, err.Error(), "Limit must be 0 (server default) or 1 to 200")
	}
}
