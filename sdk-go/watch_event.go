// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"time"
)

// FilesystemEvent is a file change or a server overflow notification.
type FilesystemEvent struct {
	// Type is the open change vocabulary, including create, write, remove,
	// rename and chmod. Future values are preserved verbatim.
	Type string
	// Name is the changed path.
	Name string
	// IsDir reports a directory change.
	IsDir bool
	// Timestamp is the server event time, decoded from Unix milliseconds.
	Timestamp time.Time
	// Overflow identifies an overflow notification rather than a file change.
	Overflow bool
	// Dropped is the server's overflow indicator, meaningful when Overflow is true.
	Dropped bool
}

//nolint:nilnil // A future SSE name is intentionally skipped without parsing its data.
func parseWatchEvent(message fileSSEMessage) (*FilesystemEvent, error) {
	switch message.event {
	case "fs", "overflow":
		// Only known data events are parsed; future event names may use other formats.
	default:
		return nil, nil
	}
	data := []byte(message.data)
	if len(data) == 0 {
		data = []byte("{}")
	}
	if err := checkJSON(data); err != nil {
		return nil, err
	}
	object, err := parseObject(data, "filesystem event")
	if err != nil {
		return nil, err
	}
	switch message.event {
	case "overflow":
		dropped, fieldErr := field[bool](object, "dropped", true)
		if fieldErr != nil {
			return nil, fieldErr
		}
		return &FilesystemEvent{Overflow: true, Dropped: dropped}, nil
	}
	kind, kindErr := field[string](object, "type", true)
	name, nameErr := field[string](object, "name", true)
	directory, dirErr := field[bool](object, "isDir", true)
	timestamp, timeErr := integerField(object, "timestamp", true)
	if parseErr := errors.Join(kindErr, nameErr, dirErr, timeErr); parseErr != nil {
		return nil, parseErr
	}
	return &FilesystemEvent{Type: kind, Name: name, IsDir: directory, Timestamp: time.UnixMilli(timestamp).UTC()}, nil
}
