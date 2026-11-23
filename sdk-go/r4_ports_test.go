// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPorts_whenReusingGrantedPorts_acceptsTokenOptions(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/access-token": jsonHandler(t, sandboxAccessFixture()),
	})
	sandbox := newMutatorSandbox(t, fake)
	token, err := sandbox.CreateAccessToken(t.Context(), nil)
	require.NoError(t, err)
	// When
	_, err = sandbox.CreateAccessToken(t.Context(), &CreateAccessTokenOptions{Ports: token.Ports})
	// Then
	require.NoError(t, err)
	require.JSONEq(t, `{"ports":[3000]}`, string(fake.requests()[1].Body))
}

func TestPorts_whenReusingAssignments_acceptsPortMappings(t *testing.T) {
	// Given
	assignment := PortAssignment{HostPort: 3000, VMPort: 8080, Protocol: "tcp"}
	// When
	mappings := []PortMapping{
		{Port: assignment.HostPort, Protocol: assignment.Protocol},
		{Port: assignment.VMPort, Protocol: assignment.Protocol},
	}
	// Then
	require.Equal(t, []PortMapping{{Port: 3000, Protocol: "tcp"}, {Port: 8080, Protocol: "tcp"}}, mappings)
}

func TestAccessToken_whenGrantedPortsAtContractBounds_preservesPorts(t *testing.T) {
	// Given
	body := sandboxAccessFixture()
	body["ports"] = []int{0, 65536, math.MaxInt32}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/access-token": jsonHandler(t, body),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	token, err := sandbox.CreateAccessToken(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, []int{0, 65536, math.MaxInt32}, token.Ports)
}

func TestAccessToken_whenGrantedPortsEmpty_preservesEmptyScope(t *testing.T) {
	// Given
	body := sandboxAccessFixture()
	body["ports"] = []int{}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sandboxes/sbx/access-token": jsonHandler(t, body),
	})
	sandbox := newMutatorSandbox(t, fake)
	// When
	token, err := sandbox.CreateAccessToken(t.Context(), nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, []int{}, token.Ports)
}
