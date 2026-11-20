// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "context"

// ComputerUseDisplays reads desktop display and window information.
type ComputerUseDisplays struct{ s *Sandbox }

// ComputerUseDisplay describes one connected display.
type ComputerUseDisplay struct {
	// ID identifies the display.
	ID int64
	// X is its horizontal pixel offset.
	X int
	// Y is its vertical pixel offset.
	Y int
	// Width is its pixel width.
	Width int
	// Height is its pixel height.
	Height int
	// IsActive reports whether it is active.
	IsActive bool
}

// ComputerUseWindow describes a desktop window.
type ComputerUseWindow struct {
	// ID identifies the window.
	ID int64
	// Title is the window title.
	Title string
	// X is its horizontal position in pixels.
	X int
	// Y is its vertical position in pixels.
	Y int
	// Width is its pixel width.
	Width int
	// Height is its pixel height.
	Height int
	// IsActive reports whether it is active.
	IsActive bool
}

// Info returns all connected displays. This list is not paginated.
func (s *ComputerUseDisplays) Info(ctx context.Context) ([]ComputerUseDisplay, error) {
	raw, err := desktopRead(ctx, s.s, toolboxRequest{Path: "/computeruse/display/info"})
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	rows := desktopField[[]JSONValue](d, "displays", true)
	result := make([]ComputerUseDisplay, 0, len(rows))
	for _, row := range rows {
		entry := decodeDesktop(row)
		result = append(result, ComputerUseDisplay{
			ID: entry.nonnegativeInteger("id"), X: entry.int32("x"), Y: entry.int32("y"),
			Width: entry.nonnegative32("width"), Height: entry.nonnegative32("height"),
			IsActive: entry.boolean("isActive"),
		})
		d.check(entry.err)
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

// Windows returns all desktop windows. This list is not paginated.
func (s *ComputerUseDisplays) Windows(ctx context.Context) ([]ComputerUseWindow, error) {
	raw, err := desktopRead(ctx, s.s, toolboxRequest{Path: "/computeruse/display/windows"})
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	rows := desktopField[[]JSONValue](d, "windows", true)
	result := make([]ComputerUseWindow, 0, len(rows))
	for _, row := range rows {
		entry := decodeDesktop(row)
		result = append(result, ComputerUseWindow{
			ID: entry.safeInteger("id"), Title: entry.text("title"),
			X: entry.int32("x"), Y: entry.int32("y"),
			Width: entry.nonnegative32("width"), Height: entry.nonnegative32("height"),
			IsActive: entry.boolean("isActive"),
		})
		d.check(entry.err)
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}
