// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"strconv"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestComputerUseR3Displays_whenIDExceedsSafeInteger(t *testing.T) {
	for _, value := range []int64{1 << 53, 1<<53 + 1, 1<<63 - 1} {
		t.Run(strconv.FormatInt(value, 10), func(t *testing.T) {
			// Given
			body := fmt.Sprintf(`{"displays":[{"id":%d,"x":0,"y":0,"width":1,"height":1,"isActive":true}]}`, value)
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
			// When
			displays, err := sandbox.ComputerUse().Displays().Info(t.Context())
			// Then
			require.NoError(t, err)
			require.Len(t, displays, 1)
			require.Equal(t, value, displays[0].ID)
		})
	}
}

func TestComputerUseR3Screenshot_whenSizeExceedsSafeInteger(t *testing.T) {
	for _, value := range []int64{1 << 53, 1<<53 + 1, 1<<63 - 1} {
		t.Run(strconv.FormatInt(value, 10), func(t *testing.T) {
			// Given
			body := fmt.Sprintf(`{"screenshot":"AA==","sizeBytes":%d}`, value)
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
			// When
			screenshot, err := sandbox.ComputerUse().Screen().Screenshot(t.Context(), nil)
			// Then
			require.NoError(t, err)
			require.Equal(t, value, screenshot.SizeBytes)
		})
	}
}

func TestComputerUseR3Integers_whenOutsideFieldRange(t *testing.T) {
	for _, value := range []string{"-1", "9223372036854775808", "1.5"} {
		t.Run("display/"+value, func(t *testing.T) {
			// Given
			body := fmt.Sprintf(`{"displays":[{"id":%s,"x":0,"y":0,"width":1,"height":1,"isActive":true}]}`, value)
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
			// When
			_, err := sandbox.ComputerUse().Displays().Info(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
		t.Run("screenshot/"+value, func(t *testing.T) {
			// Given
			body := fmt.Sprintf(`{"screenshot":"AA==","sizeBytes":%s}`, value)
			sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
			// When
			_, err := sandbox.ComputerUse().Screen().Screenshot(t.Context(), nil)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
	t.Run("window/above safe integer", func(t *testing.T) {
		// Given
		body := `{"windows":[{"id":9007199254740992,"title":"","x":0,"y":0,"width":1,"height":1,"isActive":true}]}`
		sandbox, _ := computerUseTestSandbox(t, jsonHandler(t, JSONValue(body)))
		// When
		_, err := sandbox.ComputerUse().Displays().Windows(t.Context())
		// Then
		require.ErrorIs(t, err, ErrBadRequest)
	})
}
