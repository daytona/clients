// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenFractionalWait_rejectsBeforeRequest(t *testing.T) {
	for _, route := range createTestCases() {
		t.Run(route.name, func(t *testing.T) {
			// Given: create holds, unlike fork/lifecycle waits, require whole seconds.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			_, err := route.call(t.Context(), client, &CreateFromSnapshotOptions{Wait: time.Millisecond})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
