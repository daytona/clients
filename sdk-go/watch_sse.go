// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"errors"
	"io"
	"strings"
)

const (
	fileWatchStartupBytes  = 1 << 20
	fileWatchStartupEvents = 4096
)

// fileSSE bounds each frame as bytes arrive, including comments and incomplete lines.
type fileSSE struct {
	reader     *bufio.Reader
	limit      int64
	started    bool
	completed  int64
	frameBytes int64
	pendingLF  bool
	first      bool
}

type fileSSEMessage struct {
	event string
	data  string
}

func (s *fileSSE) byte() (byte, error) {
	value, err := s.reader.ReadByte()
	if err != nil {
		return 0, err
	}
	skip := s.pendingLF && value == '\n'
	s.pendingLF = false
	if skip {
		if s.frameBytes == 0 {
			s.completed++
		} else {
			s.frameBytes++
		}
		if boundErr := s.checkBound(); boundErr != nil {
			return 0, boundErr
		}
		value, err = s.reader.ReadByte()
		if err != nil {
			return 0, err
		}
	}
	s.frameBytes++
	if boundErr := s.checkBound(); boundErr != nil {
		return 0, boundErr
	}
	return value, nil
}

func (s *fileSSE) checkBound() error {
	if !s.started && s.completed+s.frameBytes > fileWatchStartupBytes {
		return badRequest("filesystem watch exceeded 1 MiB before start")
	}
	if s.limit > 0 && s.frameBytes > s.limit {
		return responseTooLarge(s.limit)
	}
	return nil
}

func (s *fileSSE) line() (string, error) {
	var line strings.Builder
	for {
		value, err := s.byte()
		if err != nil {
			return "", err
		}
		switch value {
		case '\r':
			s.pendingLF = true
			return line.String(), nil
		case '\n':
			return line.String(), nil
		default:
			line.WriteByte(value)
		}
	}
}

func (s *fileSSE) next() (fileSSEMessage, error) {
	event := "message"
	var data []string
	for {
		line, err := s.line()
		if err != nil {
			return fileSSEMessage{}, err
		}
		if !s.first {
			line = strings.TrimPrefix(line, "\ufeff")
			s.first = true
		}
		if line == "" {
			s.completed += s.frameBytes
			s.frameBytes = 0
			if data != nil {
				return fileSSEMessage{event: event, data: strings.Join(data, "\n")}, nil
			}
			event = "message"
			continue
		}
		name, value, _ := strings.Cut(line, ":")
		value = strings.TrimPrefix(value, " ")
		switch name {
		case "event":
			event = value
		case "data":
			data = append(data, value)
		default:
			// Comments, ids, retry hints and future fields are ignored.
		}
	}
}

func fileWatchReadError(ctxErr error, readErr error) error {
	if ctxErr != nil {
		return ctxErr
	}
	if errors.Is(readErr, io.EOF) {
		return &Error{Source: "SDK", Message: "filesystem watch stream ended unexpectedly", category: ErrConnection}
	}
	var sdkError *Error
	if errors.As(readErr, &sdkError) {
		return readErr
	}
	return &Error{Source: "SDK", Message: "filesystem watch stream failed", Err: readErr, category: ErrConnection}
}
