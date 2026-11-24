// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPTYCreateRollsBack_whenAcceptedRecordMalformed(t *testing.T) {
	for _, cleanupStatus := range []int{http.StatusNoContent, http.StatusServiceUnavailable} {
		t.Run(http.StatusText(cleanupStatus), func(t *testing.T) {
			// Given an accepted PTY whose record cannot be parsed.
			info := ptyFixture("pty")
			info["state"] = 7
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes": jsonHandler(t, info),
				"DELETE /processes/p": func(w http.ResponseWriter, _ *http.Request) {
					w.WriteHeader(cleanupStatus)
				},
			})
			// When creating an attached terminal.
			connection, err := sandbox.PTY().Create(t.Context(), "", nil)
			// Then rollback is attempted and never replaces the original parse error.
			require.Nil(t, connection)
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "state")
			require.Len(t, fake.requests(), 2)
			require.Equal(t, http.MethodDelete, fake.requests()[1].Method)
		})
	}
}

func TestCreateSessionPreservesIdentity_whenAcceptedRecordMalformed(t *testing.T) {
	// Given an accepted persistent shell with an invalid timestamp.
	info := processFixture("shell")
	info["kind"], info["createdAt"] = "session", "invalid"
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes": jsonHandler(t, info),
	})
	// When creating the session.
	session, err := sandbox.Process().CreateSession(t.Context(), nil)
	// Then the caller can reconnect or delete the accepted shell despite the error.
	require.ErrorIs(t, err, ErrBadRequest)
	require.NotNil(t, session)
	require.Equal(t, SessionJSON{SandboxID: "sbx", SessionID: "shell"}, session.ToJSON())
	require.Len(t, fake.requests(), 1)
}

func TestToolboxCreateReturnsNil_whenIdentityMalformed(t *testing.T) {
	for _, entry := range []string{"pty", "session"} {
		t.Run(entry, func(t *testing.T) {
			// Given a record without a usable process identity.
			info := processFixture("..")
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes": jsonHandler(t, info),
			})
			// When creating a terminal or persistent session.
			switch entry {
			case "pty":
				connection, err := sandbox.PTY().Create(t.Context(), "", nil)
				// Then no unsafe identity is used for cleanup.
				require.ErrorIs(t, err, ErrBadRequest)
				require.Nil(t, connection)
			case "session":
				session, err := sandbox.Process().CreateSession(t.Context(), nil)
				// Then no unusable session handle is returned.
				require.ErrorIs(t, err, ErrBadRequest)
				require.Nil(t, session)
			}
			require.Len(t, fake.requests(), 1)
		})
	}
}
