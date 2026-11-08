// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

type rawItem struct {
	ID    string `json:"id"`
	Count int    `json:"count"`
}

func TestRaw_marshalsTheJSONItWasDecodedFrom(t *testing.T) {
	r, err := DecodeRaw[rawItem]([]byte(`{"id":"a","future":{"n":12345678901234567890}}`))
	require.NoError(t, err)
	require.Equal(t, "a", r.V.ID)
	out, err := json.Marshal(r)
	require.NoError(t, err)
	require.JSONEq(t, `{"id":"a","future":{"n":12345678901234567890}}`, string(out))

	built, err := json.Marshal(Raw[rawItem]{V: rawItem{ID: "b"}})
	require.NoError(t, err)
	require.JSONEq(t, `{"id":"b","count":0}`, string(built))
}

func TestDecodePage(t *testing.T) {
	page, err := DecodePage[rawItem]([]byte(`{"things":[{"id":"a","x":1}],"next_cursor":"c"}`), "things")
	require.NoError(t, err)
	require.Equal(t, "c", page.NextCursor)
	require.Equal(t, []rawItem{{ID: "a"}}, Values(page.Items))

	for _, body := range []string{``, `{}`, `{"things":null,"next_cursor":null}`} {
		page, err = DecodePage[rawItem]([]byte(body), "things")
		require.NoError(t, err, body)
		require.Empty(t, page.Items, body)
		require.NotNil(t, page.Items, body)
		require.Empty(t, page.NextCursor, body)
	}
	_, err = DecodePage[rawItem]([]byte(`[]`), "things")
	require.Error(t, err)
}
