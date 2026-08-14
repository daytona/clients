// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestUTF8StreamDecoder_holds_split_multibyte_rune(t *testing.T) {
	// Given
	decoder := utf8StreamDecoder{}

	// When
	first := decoder.Decode([]byte{0xf0, 0x9f})
	second := decoder.Decode([]byte{0x98, 0x80, ' ', 'o', 'k'})

	// Then
	require.Empty(t, first)
	require.Equal(t, "😀 ok", second)
	require.Empty(t, decoder.Flush())
}

func TestUTF8StreamDecoder_flush_replaces_dangling_bytes(t *testing.T) {
	// Given
	decoder := utf8StreamDecoder{}

	// When
	decoded := decoder.Decode([]byte{0xf0, 0x9f})
	tail := decoder.Flush()

	// Then
	require.Empty(t, decoded)
	require.Equal(t, "�", tail)
}
