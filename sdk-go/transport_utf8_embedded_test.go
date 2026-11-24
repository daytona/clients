// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

type utf8Embedded struct {
	Text string `json:"text"`
}

func TestTransport_whenUnexportedEmbeddedStringIsInvalid_rejectsBeforeAuthentication(t *testing.T) {
	for _, test := range []struct {
		name string
		body any
		path string
	}{
		{"value", struct{ utf8Embedded }{utf8Embedded{Text: "private\xff"}}, "text"},
		{"pointer", struct{ *utf8Embedded }{&utf8Embedded{Text: "private\xff"}}, "text"},
		{"named", struct {
			utf8Embedded `json:"nested"`
		}{utf8Embedded{Text: "private\xff"}}, "nested.text"},
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
			require.ErrorContains(t, err, test.path)
			require.NotContains(t, err.Error(), "private")
			require.Empty(t, fake.requests())
		})
	}
}

func TestRequestUTF8_whenUnexportedFieldsAreNotEncoded_ignoresThem(t *testing.T) {
	type hiddenText string
	for _, test := range []struct {
		name string
		body any
	}{
		{"ordinary struct", struct{ hidden utf8Embedded }{utf8Embedded{Text: "\xff"}}},
		{"embedded scalar", struct{ hiddenText }{hiddenText("\xff")}},
		{"ignored embedded struct", struct {
			utf8Embedded `json:"-"`
		}{utf8Embedded{Text: "\xff"}}},
		{"nil embedded pointer", struct{ *utf8Embedded }{}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			input := test.body
			// When
			err := checkRequestUTF8(input)
			// Then
			require.NoError(t, err)
		})
	}
}
