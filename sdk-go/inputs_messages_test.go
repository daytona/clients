// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

// The checks the Python and TypeScript SDKs share, with the same messages
// modulo Go's field names (tests/test_inputs_and_messages.py,
// tests/inputs-and-messages.test.ts).

func requireBadRequestMessage(t *testing.T, err error, message string) {
	t.Helper()
	require.ErrorIs(t, err, ErrBadRequest)
	var sdkErr *Error
	require.ErrorAs(t, err, &sdkErr)
	require.Equal(t, message, sdkErr.Message)
}

func TestCreate_whenEnvBreaksTheUpdateEnvRule(t *testing.T) {
	for _, test := range []struct {
		env     map[string]string
		message string
	}{
		{map[string]string{"A=B": "x"}, "env key is not a valid variable name"},
		{map[string]string{"": "x"}, "env key is not a valid variable name"},
		{map[string]string{"A\nB": "x"}, "env key is not a valid variable name"},
		{map[string]string{"A": "x\x00y"}, "env values must not contain NUL"},
	} {
		// Given
		fake := newFakeServer(t, nil)
		client := newTestClient(t, fake.Server)
		// When
		_, linuxErr := client.Create(t.Context(), "img", &CreateOptions{Env: test.env})
		_, macErr := client.CreateMacOS(t.Context(), "base-macos", &CreateMacOSOptions{Env: test.env})
		// Then
		requireBadRequestMessage(t, linuxErr, test.message)
		requireBadRequestMessage(t, macErr, test.message)
		require.Empty(t, fake.requests())
	}
}

func TestIdempotencyKey_whenLongerThan256Bytes(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	key := strings.Repeat("x", 257)
	// When
	_, createErr := client.Create(t.Context(), "img", &CreateOptions{IdempotencyKey: key})
	_, reserveErr := client.Reservations().Create(t.Context(), "r", &CreateReservationOptions{IdempotencyKey: key})
	_, renewErr := client.Reservations().Renew(t.Context(), "res", &RenewReservationOptions{IdempotencyKey: key})
	// Then
	for _, err := range []error{createErr, reserveErr, renewErr} {
		requireBadRequestMessage(t, err, "IdempotencyKey must be at most 256 bytes")
	}
	require.Empty(t, fake.requests())
}

func TestCreate_sendsA256ByteKeyAsTheHeaderOnly(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /sandboxes": jsonHandler(t, map[string]string{"id": "sbx"})})
	client := newTestClient(t, fake.Server)
	key := strings.Repeat("k", 256)
	// When
	_, err := client.Create(t.Context(), "img", &CreateOptions{Wait: NoWait, IdempotencyKey: key})
	// Then
	require.NoError(t, err)
	request := fake.requests()[0]
	require.Equal(t, key, request.Header.Get("Idempotency-Key"))
	require.NotContains(t, string(request.Body), "idempotency_key")
}

func TestScreenshot_whenRegionIsEmpty(t *testing.T) {
	for _, test := range []struct {
		region  ComputerUseRegion
		message string
	}{
		{ComputerUseRegion{Width: 0, Height: 1}, "Region.Width must be greater than 0"},
		{ComputerUseRegion{Width: 1, Height: -2}, "Region.Height must be greater than 0"},
		{ComputerUseRegion{X: 1 << 53, Width: 1, Height: 1}, "Region.X must be between -9007199254740991 and 9007199254740991"},
	} {
		// Given
		sandbox, fake := computerUseTestSandbox(t, jsonHandler(t, JSONValue(`{"screenshot":"AA==","sizeBytes":1}`)))
		before := len(fake.requests())
		// When
		_, err := sandbox.ComputerUse().Screen().Screenshot(t.Context(), &ComputerUseScreenshotOptions{Region: &test.region})
		// Then
		requireBadRequestMessage(t, err, test.message)
		require.Len(t, fake.requests(), before)
	}
}

func TestNewImageBuilder_whenTheReferenceHasWhitespace(t *testing.T) {
	for _, ref := range []string{"python 3", "python:3.12 AS base", "py\u00a0thon", "py\u2003thon"} {
		_, err := NewImageBuilder(ref).Dockerfile()
		requireBadRequestMessage(t, err, "image base must not contain whitespace")
	}
	for _, ref := range []string{
		"python:3.12-slim", "  python:3.12  ", "registry.example.com:5000/team/app:v1",
		"ghcr.io/org/app@sha256:" + strings.Repeat("a", 64),
	} {
		text, err := NewImageBuilder(ref).Dockerfile()
		require.NoError(t, err)
		require.Equal(t, "FROM "+strings.TrimSpace(ref)+"\n", text)
	}
}

func TestSearch_whenStatesAreEmptyOrUnknown(t *testing.T) {
	for _, test := range []struct {
		states  []SandboxState
		message string
	}{
		{[]SandboxState{}, "states must name at least one sandbox state"},
		{[]SandboxState{SandboxStateUnknown}, `states entries must be known sandbox states, got "unknown"`},
		{[]SandboxState{SandboxStateStarted, "running"}, `states entries must be known sandbox states, got "running"`},
		{[]SandboxState{"m\u00e9t\u00e9o\t<x>"}, `states entries must be known sandbox states, got "météo\t<x>"`},
	} {
		// Given
		fake := newFakeServer(t, nil)
		client := newTestClient(t, fake.Server)
		// When
		_, err := client.Search(t.Context(), &SearchSandboxesOptions{States: test.states})
		// Then
		requireBadRequestMessage(t, err, test.message)
		require.Empty(t, fake.requests())
	}
}

func TestUploadFile_whenTheSourceIsADirectory(t *testing.T) {
	// Given
	fake := newFakeServer(t, nil)
	fs := newFileTestService(t, fake.Server)
	// When
	err := fs.UploadFile(t.Context(), t.TempDir(), "/dest", nil)
	// Then
	requireBadRequestMessage(t, err, "localPath must be a regular file")
	require.Empty(t, fake.requests())
}

func TestCreate_whenAPortIsInvalid(t *testing.T) {
	for _, test := range []struct {
		port    PortMapping
		message string
	}{
		{PortMapping{Port: 0}, "ports[].port must be an integer in 1..65535"},
		{PortMapping{Port: 80, Protocol: "sctp"}, "ports[].protocol must be 'tcp' or 'udp'"},
	} {
		// Given
		fake := newFakeServer(t, nil)
		client := newTestClient(t, fake.Server)
		// When
		_, err := client.Create(t.Context(), "img", &CreateOptions{Ports: []PortMapping{test.port}})
		// Then
		requireBadRequestMessage(t, err, test.message)
		require.Empty(t, fake.requests())
	}
}
