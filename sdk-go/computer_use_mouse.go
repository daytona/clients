// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// ComputerUseMouse controls the desktop pointer.
type ComputerUseMouse struct{ s *Sandbox }

// ComputerUsePoint is a location in desktop pixels.
type ComputerUsePoint struct {
	// X is the horizontal position in pixels.
	X int `json:"x"`
	// Y is the vertical position in pixels.
	Y int `json:"y"`
}

// ComputerUseMouseButton selects a pointer button.
type ComputerUseMouseButton string

const (
	// ComputerUseMouseButtonUnknown is an unrecognized button.
	ComputerUseMouseButtonUnknown ComputerUseMouseButton = "unknown"
	// ComputerUseMouseButtonLeft is the primary button.
	ComputerUseMouseButtonLeft ComputerUseMouseButton = "left"
	// ComputerUseMouseButtonRight is the secondary button.
	ComputerUseMouseButtonRight ComputerUseMouseButton = "right"
	// ComputerUseMouseButtonMiddle is the middle button.
	ComputerUseMouseButtonMiddle ComputerUseMouseButton = "middle"
)

// ComputerUseScrollDirection selects vertical scrolling.
type ComputerUseScrollDirection string

const (
	// ComputerUseScrollUnknown is an unrecognized direction.
	ComputerUseScrollUnknown ComputerUseScrollDirection = "unknown"
	// ComputerUseScrollUp scrolls upward.
	ComputerUseScrollUp ComputerUseScrollDirection = "up"
	// ComputerUseScrollDown scrolls downward.
	ComputerUseScrollDown ComputerUseScrollDirection = "down"
)

// ComputerUseClickOptions selects a mouse button and click count.
type ComputerUseClickOptions struct {
	// Button defaults to left.
	Button ComputerUseMouseButton `json:"button,omitempty"`
	// Double performs a double click when true.
	Double bool `json:"double,omitempty"`
}

// ComputerUseDrag is a pointer movement between two desktop locations.
type ComputerUseDrag struct {
	// Start is the initial pointer location.
	Start ComputerUsePoint
	// End is the destination.
	End ComputerUsePoint
}

// ComputerUseDragOptions selects the held button.
type ComputerUseDragOptions struct {
	// Button defaults to left.
	Button ComputerUseMouseButton
}

// ComputerUseScroll is a scroll gesture at a desktop point.
type ComputerUseScroll struct {
	// Position is where to scroll.
	Position ComputerUsePoint
	// Direction is up or down.
	Direction ComputerUseScrollDirection
}

// ComputerUseScrollOptions controls scroll distance.
type ComputerUseScrollOptions struct {
	// Amount is the scroll amount; nil uses the server default.
	Amount *int
}

// ComputerUseScrollResult reports whether scrolling succeeded.
type ComputerUseScrollResult struct {
	// Success is the server-reported result.
	Success bool
}

// Position reads the pointer's current pixel coordinates.
func (m *ComputerUseMouse) Position(ctx context.Context) (*ComputerUsePoint, error) {
	raw, err := desktopRead(ctx, m.s, toolboxRequest{Path: "/computeruse/mouse/position"})
	if err != nil {
		return nil, err
	}
	return parseDesktopPoint(raw)
}

// Move moves the pointer to position and returns the resulting coordinates.
func (m *ComputerUseMouse) Move(ctx context.Context, position ComputerUsePoint) (*ComputerUsePoint, error) {
	raw, err := desktopRead(ctx, m.s, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/mouse/move", Body: position,
	})
	if err != nil {
		return nil, err
	}
	return parseDesktopPoint(raw)
}

// Click clicks at position. Nil options select a single left click.
func (m *ComputerUseMouse) Click(
	ctx context.Context, position ComputerUsePoint, opts *ComputerUseClickOptions,
) (*ComputerUsePoint, error) {
	body := struct {
		ComputerUsePoint
		*ComputerUseClickOptions
	}{ComputerUsePoint: position, ComputerUseClickOptions: opts}
	raw, err := desktopRead(ctx, m.s, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/mouse/click", Body: body,
	})
	if err != nil {
		return nil, err
	}
	return parseDesktopPoint(raw)
}

// Drag holds a button while dragging between two points.
func (m *ComputerUseMouse) Drag(
	ctx context.Context, gesture ComputerUseDrag, opts *ComputerUseDragOptions,
) (*ComputerUsePoint, error) {
	body := struct {
		StartX int                    `json:"startX"`
		StartY int                    `json:"startY"`
		EndX   int                    `json:"endX"`
		EndY   int                    `json:"endY"`
		Button ComputerUseMouseButton `json:"button,omitempty"`
	}{StartX: gesture.Start.X, StartY: gesture.Start.Y, EndX: gesture.End.X, EndY: gesture.End.Y}
	if opts != nil {
		body.Button = opts.Button
	}
	raw, err := desktopRead(ctx, m.s, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/mouse/drag", Body: body,
	})
	if err != nil {
		return nil, err
	}
	return parseDesktopPoint(raw)
}

// Scroll scrolls at a point. Nil options use the server's default amount.
func (m *ComputerUseMouse) Scroll(
	ctx context.Context, gesture ComputerUseScroll, opts *ComputerUseScrollOptions,
) (*ComputerUseScrollResult, error) {
	body := struct {
		ComputerUsePoint
		Direction ComputerUseScrollDirection `json:"direction"`
		Amount    *int                       `json:"amount,omitempty"`
	}{ComputerUsePoint: gesture.Position, Direction: gesture.Direction}
	if opts != nil {
		body.Amount = opts.Amount
	}
	raw, err := desktopRead(ctx, m.s, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/mouse/scroll", Body: body,
	})
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	result := &ComputerUseScrollResult{Success: d.boolean("success")}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}
