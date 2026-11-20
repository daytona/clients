// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestInterpreterR3MarshalJSON_whenHandleCopied_preservesIdentity(t *testing.T) {
	// Given a copied context handle bound to an authenticated sandbox.
	fake := newFakeServer(t, nil)
	bound, err := interpreterTestSandbox(t, fake).Interpreter().ConnectContext("a/b", "python")
	require.NoError(t, err)
	// When encoding a value rather than its pointer.
	encoded, err := json.Marshal(*bound)
	// Then only its cross-SDK, credential-free identity is serialized.
	require.NoError(t, err)
	require.JSONEq(t, `{"sandboxId":"sbx","contextId":"a/b","language":"python"}`, string(encoded))
}
