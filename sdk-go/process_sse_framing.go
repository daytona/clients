// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "bufio"

// consumeBufferedSSELF consumes an optional LF after CR without reading from the
// underlying stream. Callers count the LF against the frame containing the CR.
func consumeBufferedSSELF(reader *bufio.Reader) (bool, error) {
	if reader.Buffered() == 0 {
		return false, nil
	}
	next, err := reader.Peek(1)
	if err != nil {
		return false, err
	}
	if next[0] != '\n' {
		return false, nil
	}
	_, err = reader.Discard(1)
	return err == nil, err
}
