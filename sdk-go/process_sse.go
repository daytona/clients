// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"errors"
	"io"
	"strings"
)

type processSSEMessage struct{ event, id, data string }

// processSSEReader counts bytes before buffering each event, including comments
// and partial lines. EOF never dispatches an unterminated SSE block.
type processSSEReader struct {
	reader *bufio.Reader
	limit  int64
	bytes  int64
	first  bool
}

func (s *processSSEReader) next() (processSSEMessage, error) {
	message := processSSEMessage{}
	var data strings.Builder
	hasData := false
	for {
		line, err := s.line()
		if err != nil {
			return processSSEMessage{}, err
		}
		if s.first {
			line = strings.TrimPrefix(line, "\ufeff")
			s.first = false
		}
		if line == "" {
			s.bytes = 0
			if hasData {
				message.data = strings.TrimSuffix(data.String(), "\n")
				return message, nil
			}
			message = processSSEMessage{}
			continue
		}
		key, value, _ := strings.Cut(line, ":")
		value = strings.TrimPrefix(value, " ")
		switch key {
		case "event":
			message.event = value
		case "id":
			if !strings.ContainsRune(value, '\x00') {
				message.id = value
			}
		case "data":
			hasData = true
			data.WriteString(value)
			data.WriteByte('\n')
		}
	}
}

func (s *processSSEReader) line() (string, error) {
	var line strings.Builder
	for {
		char, err := s.reader.ReadByte()
		if err != nil {
			return "", err
		}
		s.bytes++
		if s.limit > 0 && s.bytes > s.limit {
			return "", newSDKError(CodePayloadTooLarge, 413, "process log event exceeds max response bytes")
		}
		switch char {
		case '\r':
			// Hold a trailing CR until its optional LF arrives, so a CRLF
			// split across reads counts both bytes toward the same event.
			next, peekErr := s.reader.Peek(1)
			if peekErr != nil && !errors.Is(peekErr, io.EOF) {
				return "", peekErr
			}
			if len(next) == 1 && next[0] == '\n' {
				if _, discardErr := s.reader.Discard(1); discardErr != nil {
					return "", discardErr
				}
				s.bytes++
				if s.limit > 0 && s.bytes > s.limit {
					return "", newSDKError(CodePayloadTooLarge, 413, "process log event exceeds max response bytes")
				}
			}
			return line.String(), nil
		case '\n':
			return line.String(), nil
		default:
			line.WriteByte(char)
		}
	}
}

func newProcessSSEReader(reader io.Reader, limit int64) *processSSEReader {
	return &processSSEReader{reader: bufio.NewReader(reader), limit: limit, first: true}
}
