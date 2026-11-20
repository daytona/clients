// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Package events implements the sandbox state subscription protocol.
package events

import (
	"encoding/json"
	"unicode/utf8"
)

// Subprotocol is the negotiated sandbox events protocol.
const Subprotocol = "daytona.events.v1"

const (
	maxClientFrame = 65536
	maxServerFrame = 1 << 20
	maxFrameIDs    = 1000
)

type frame struct {
	Type     string  `json:"type"`
	ID       *string `json:"id"`
	Status   string  `json:"status"`
	Version  float64 `json:"version"`
	Reason   string  `json:"error"`
	Code     string  `json:"code"`
	WasKnown bool    `json:"was_known"`
}

// Decode only the fields of a recognized frame; unknown extensions are inert.
func decodeFrame(data []byte) (frame, bool) {
	var header struct {
		Type string `json:"type"`
	}
	if !boundedJSON(data) || json.Unmarshal(data, &header) != nil {
		return frame{}, false
	}
	switch header.Type {
	case "ping", "pong":
		return frame{Type: header.Type}, true
	case "snapshot", "event":
		var state struct {
			ID      *string  `json:"id"`
			Status  string   `json:"status"`
			Version *float64 `json:"version"`
			Reason  string   `json:"error"`
		}
		if json.Unmarshal(data, &state) != nil || state.ID == nil || state.Version == nil {
			return frame{}, false
		}
		return frame{
			Type: header.Type, ID: state.ID, Status: state.Status, Version: *state.Version, Reason: state.Reason,
		}, true
	case "error":
		var failure struct {
			ID       *string `json:"id"`
			Code     string  `json:"code"`
			WasKnown bool    `json:"was_known"`
		}
		if json.Unmarshal(data, &failure) != nil {
			return frame{}, false
		}
		return frame{Type: header.Type, ID: failure.ID, Code: failure.Code, WasKnown: failure.WasKnown}, true
	default:
		return frame{}, false
	}
}

func boundedJSON(data []byte) bool {
	if !utf8.Valid(data) || len(data) > maxServerFrame {
		return false
	}
	depth, quoted, escaped := 0, false, false
	for _, char := range data {
		switch {
		case escaped:
			escaped = false
		case quoted && char == '\\':
			escaped = true
		case char == '"':
			quoted = !quoted
		case !quoted && (char == '{' || char == '['):
			depth++
			if depth > 600 {
				return false
			}
		case !quoted && (char == '}' || char == ']'):
			depth--
		}
	}
	return true
}

type subscription struct {
	ID    string `json:"id"`
	Known bool   `json:"known"`
}

// Packing by bytes as well as count matters for long IDs and JSON escapes.
func subscriptionFrames(kind string, ids []subscription) ([][]byte, error) {
	prefix := []byte(`{"type":"` + kind + `","ids":[`)
	var frames [][]byte
	current := append([]byte(nil), prefix...)
	count := 0
	for _, id := range ids {
		var encoded []byte
		var err error
		switch kind {
		case "subscribe":
			encoded, err = json.Marshal(id)
		default:
			encoded, err = json.Marshal(id.ID)
		}
		if err != nil {
			return nil, err
		}
		if count > 0 && (count == maxFrameIDs || len(current)+len(encoded)+3 > maxClientFrame) {
			frames = append(frames, append(current, ']', '}'))
			current = append([]byte(nil), prefix...)
			count = 0
		}
		if count > 0 {
			current = append(current, ',')
		}
		current = append(current, encoded...)
		count++
	}
	if count > 0 {
		frames = append(frames, append(current, ']', '}'))
	}
	return frames, nil
}
