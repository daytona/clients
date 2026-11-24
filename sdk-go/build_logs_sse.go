// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"io"
	"strings"
	"unicode/utf8"
)

type buildLogMessage struct {
	name string
	id   *string
	data string
}

type buildLogDecoder struct {
	reader    *bufio.Reader
	afterCR   bool
	firstLine bool
	size      int
}

func (d *buildLogDecoder) next() (buildLogMessage, error) {
	message := buildLogMessage{name: "message"}
	var data []string
	for {
		line, err := d.line()
		if err != nil {
			return buildLogMessage{}, err // EOF never dispatches a partial event.
		}
		if line == "" {
			d.size = 0
			if len(data) > 0 {
				message.data = strings.Join(data, "\n")
				return message, nil
			}
			if message.id != nil {
				return buildLogMessage{}, buildLogProtocol("event ID without data")
			}
			message = buildLogMessage{name: "message"}
			continue
		}
		key, value, colon := strings.Cut(line, ":")
		value = strings.TrimPrefix(value, " ")
		switch key {
		case "event":
			message.name = value
		case "id":
			if message.id != nil || !colon {
				return buildLogMessage{}, buildLogProtocol("duplicate or missing SSE ID")
			}
			message.id = &value
		case "data":
			data = append(data, value)
		}
	}
}

func (d *buildLogDecoder) line() (string, error) {
	var line []byte
	for {
		value, err := d.reader.ReadByte()
		if err != nil {
			return "", err
		}
		if d.afterCR && value == '\n' && d.size == 0 {
			d.afterCR = false
			continue
		}
		d.size++
		if d.size > buildLogMaxEvent || value == '\r' && d.size == buildLogMaxEvent {
			return "", buildLogProtocol("SSE event exceeds 128 KiB")
		}
		if d.afterCR {
			d.afterCR = false
			if value == '\n' {
				continue
			}
		}
		if value != '\r' && value != '\n' {
			line = append(line, value)
			continue
		}
		d.afterCR = value == '\r'
		if !utf8.Valid(line) {
			return "", buildLogProtocol("invalid SSE UTF-8")
		}
		text := string(line)
		if !d.firstLine {
			text = strings.TrimPrefix(text, "\uFEFF")
			d.firstLine = true
		}
		return text, nil
	}
}

type buildLogRetry struct{ cause error }

func (e *buildLogRetry) Error() string { return e.cause.Error() }
func (e *buildLogRetry) Unwrap() error { return e.cause }

func buildLogEOF() error {
	return &buildLogRetry{cause: io.ErrUnexpectedEOF}
}
