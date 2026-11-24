// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

type utf8TextKey struct {
	text    string
	Ignored string
}

func (key utf8TextKey) MarshalText() ([]byte, error) {
	return []byte(key.text), nil
}

type utf8IntegerTextKey int

func (utf8IntegerTextKey) MarshalText() ([]byte, error) {
	return []byte("encoded\xff"), nil
}

type utf8StringTextKey string

func (utf8StringTextKey) MarshalText() ([]byte, error) {
	return []byte("ignored\xff"), nil
}

type utf8FailingTextKey struct{ err error }

func (key utf8FailingTextKey) MarshalText() ([]byte, error) {
	return nil, key.err
}

func TestTransport_whenMarshaledMapKeyIsInvalidUTF8_rejectsBeforeAuthentication(t *testing.T) {
	for _, test := range []struct {
		name string
		body any
	}{
		{"struct", map[utf8TextKey]string{{text: "encoded\xff"}: "value"}},
		{"pointer", map[*utf8TextKey]string{{text: "encoded\xff"}: "value"}},
		{"integer marshaler", map[utf8IntegerTextKey]string{1: "value"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			wire := transport{owner: client, baseURL: fake.Server.URL, auth: invalidBodyCredential{t}}

			// When
			err := wire.do(t.Context(), apiRequest{Path: "/echo", Body: test.body})

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "map key")
			require.ErrorContains(t, err, "UTF-8")
			require.Empty(t, fake.requests())
		})
	}
}

func TestTransport_whenMapKeyUsesJSONEncodingRules_sendsExpectedKey(t *testing.T) {
	for _, test := range []struct {
		name string
		body any
		json string
	}{
		{"text only", map[utf8TextKey]string{{text: "世界", Ignored: "\xff"}: "value"}, `{"世界":"value"}`},
		{"nil pointer", map[*utf8TextKey]string{nil: "value"}, `{"":"value"}`},
		{"integer", map[int]string{42: "value"}, `{"42":"value"}`},
		{"string ignores marshaler", map[utf8StringTextKey]string{"plain": "value"}, `{"plain":"value"}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /echo": jsonHandler(t, nil)})
			client := newTestClient(t, fake.Server)

			// When
			err := client.api().do(t.Context(), apiRequest{Method: http.MethodPost, Path: "/echo", Body: test.body})

			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, test.json, string(requests[0].Body))
		})
	}
}

func TestTransport_whenMapKeyMarshalerFails_preservesCauseBeforeAuthentication(t *testing.T) {
	// Given
	cause := errors.New("key encoding failed")
	body := map[utf8FailingTextKey]string{{err: cause}: "value"}
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	wire := transport{owner: client, baseURL: fake.Server.URL, auth: invalidBodyCredential{t}}

	// When
	err := wire.do(t.Context(), apiRequest{Path: "/echo", Body: body})

	// Then
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorIs(t, err, cause)
	require.Empty(t, fake.requests())
}
