// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"io"
	"strings"
)

type processSSEMessage struct{ event, id, data string }

// processSSEReader counts bytes before buffering each event, including comments
// and partial lines. EOF never dispatches an unterminated SSE block.
type processSSEReader struct {
	reader    *bufio.Reader
	limit     int64
	bytes     int64
	first     bool
	pendingLF bool
	// Keep the completed frame's count until its optional LF is resolved.
	frameEnded bool
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
			s.frameEnded = s.pendingLF
			if !s.frameEnded {
				s.bytes = 0
			}
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

func (s *processSSEReader) byte() (byte, error) {
	for {
		char, err := s.reader.ReadByte()
		if err != nil {
			return 0, err
		}
		if s.pendingLF {
			s.pendingLF = false
			if char == '\n' {
				s.bytes++
				if boundErr := s.checkBound(); boundErr != nil {
					return 0, boundErr
				}
				continue
			}
		}
		if s.frameEnded {
			s.bytes = 0
			s.frameEnded = false
		}
		s.bytes++
		if boundErr := s.checkBound(); boundErr != nil {
			return 0, boundErr
		}
		return char, nil
	}
}

func (s *processSSEReader) line() (string, error) {
	var line strings.Builder
	for {
		char, err := s.byte()
		if err != nil {
			return "", err
		}
		switch char {
		case '\r':
			// Never wait for an optional LF. A deferred LF still belongs to
			// this frame, whose byte count survives until the next non-LF.
			s.pendingLF = s.reader.Buffered() == 0
			consumed, readErr := consumeBufferedSSELF(s.reader)
			if readErr != nil {
				return "", readErr
			}
			if consumed {
				s.bytes++
				if boundErr := s.checkBound(); boundErr != nil {
					return "", boundErr
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

func (s *processSSEReader) checkBound() error {
	if s.limit > 0 && s.bytes > s.limit {
		return newSDKError(CodePayloadTooLarge, 413, "process log event exceeds max response bytes")
	}
	return nil
}

func newProcessSSEReader(reader io.Reader, limit int64) *processSSEReader {
	return &processSSEReader{reader: bufio.NewReader(reader), limit: limit, first: true}
}
