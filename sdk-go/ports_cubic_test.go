// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPortsList_whenListenerPortIsNegative(t *testing.T) {
	// Given: PortList.ports.items has minimum 0, including at the int32 boundary.
	for _, port := range []int{-1, -2147483648} {
		t.Run(strconv.Itoa(port), func(t *testing.T) {
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /toolbox/port": jsonHandler(t, map[string][]int{"ports": {port}}),
			})
			sandbox := portsTestSandbox(t, fake)
			// When
			_, err := sandbox.Ports().List(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "SDK", sdkError.Source)
			require.ErrorContains(t, err, "ports")
		})
	}
}
