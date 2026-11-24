// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxAccessTokenRejectsReservedPortsBeforeIO(t *testing.T) {
	for _, port := range []int{2280, 2281, 22220} {
		t.Run(strconv.Itoa(port), func(t *testing.T) {
			// Given a successful API so only local validation can reject the port.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /sandboxes/sbx/access-token": jsonHandler(t, sandboxAccessFixture()),
			})
			sandbox := newMutatorSandbox(t, fake)
			// When
			_, err := sandbox.CreateAccessToken(t.Context(), &CreateAccessTokenOptions{Ports: []int{port}})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
