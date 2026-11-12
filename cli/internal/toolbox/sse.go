// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"strings"
)

type sseMessage struct {
	Event string
	Data  string
	ID    string
}

// maxSSELine / maxSSEEvent bound what one line / one event may buffer: log
// frames are small, and an endless line from a hostile or broken proxy must
// not grow memory without limit.
const (
	maxSSELine  = 4 << 20
	maxSSEEvent = 16 << 20
)

func readSSE(r io.Reader, fn func(sseMessage) error) error {
	reader := bufio.NewReader(r)
	var block bytes.Buffer
	var line bytes.Buffer
	lastEventID := ""
	for {
		if line.Len() > maxSSELine {
			return fmt.Errorf("SSE line exceeds %d bytes", maxSSELine)
		}
		if block.Len() > maxSSEEvent {
			return fmt.Errorf("SSE event exceeds %d bytes", maxSSEEvent)
		}
		one, err := reader.ReadByte()
		if err != nil && err != io.EOF {
			return fmt.Errorf("read SSE: %w", err)
		}
		if err == io.EOF {
			if line.Len() > 0 {
				trimmed := bytes.TrimRight(line.Bytes(), "\r\n")
				if block.Len()+len(trimmed)+1 > maxSSEEvent {
					return fmt.Errorf("SSE event exceeds %d bytes", maxSSEEvent)
				}
				block.Write(trimmed)
				block.WriteByte('\n')
			}
			break
		}
		if one != '\n' && one != '\r' {
			line.WriteByte(one)
			continue
		}
		if one == '\r' {
			if next, nextErr := reader.ReadByte(); nextErr == nil {
				if next != '\n' {
					_ = reader.UnreadByte()
				}
			} else if nextErr != io.EOF {
				return fmt.Errorf("read SSE: %w", nextErr)
			}
		}
		trimmed := bytes.TrimRight(line.Bytes(), "\r\n")
		if len(trimmed) == 0 {
			if block.Len() > 0 {
				msg := parseSSEMessage(block.String())
				block.Reset()
				if msg.ID != "" {
					lastEventID = msg.ID
				}
				if msg.Data != "" {
					if msg.ID == "" {
						msg.ID = lastEventID
					}
					if callErr := fn(msg); callErr != nil {
						return callErr
					}
				}
			}
		} else {
			block.Write(trimmed)
			block.WriteByte('\n')
		}
		line.Reset()
	}
	if block.Len() == 0 {
		return nil
	}
	msg := parseSSEMessage(block.String())
	if msg.ID != "" {
		lastEventID = msg.ID
	}
	if msg.Data == "" {
		return nil
	}
	if msg.ID == "" {
		msg.ID = lastEventID
	}
	return fn(msg)
}

func parseSSEMessage(raw string) sseMessage {
	msg := sseMessage{Event: "message"}
	var data []string
	for _, line := range strings.Split(strings.ReplaceAll(raw, "\r", ""), "\n") {
		if line == "" || strings.HasPrefix(line, ":") {
			continue
		}
		field, value, ok := strings.Cut(line, ":")
		if ok && strings.HasPrefix(value, " ") {
			value = value[1:]
		}
		if !ok {
			value = ""
		}
		switch field {
		case "event":
			msg.Event = value
		case "id":
			msg.ID = value
		case "data":
			data = append(data, value)
		}
	}
	msg.Data = strings.Join(data, "\n")
	if msg.ID == "" && msg.Data == "" && msg.Event == "message" {
		return sseMessage{}
	}
	return msg
}
