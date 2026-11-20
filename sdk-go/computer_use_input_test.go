// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestComputerUseMouse_whenInputSent(t *testing.T) {
	point := ComputerUsePoint{X: 12, Y: 24}
	response := `{"x":12,"y":24}`
	runDesktopWireCases(t, []desktopWireCase{
		{
			"position", "GET", "/computeruse/mouse/position", "", response,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Mouse().Position(ctx)
				if err == nil {
					require.Equal(t, point, *got)
				}
				return err
			},
		},
		{
			"move", "POST", "/computeruse/mouse/move", `{"x":12,"y":24}`, response,
			func(ctx context.Context, c *ComputerUse) error { _, err := c.Mouse().Move(ctx, point); return err },
		},
		{
			"click defaults", "POST", "/computeruse/mouse/click", `{"x":12,"y":24}`, response,
			func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Mouse().Click(ctx, point, nil)
				return err
			},
		},
		{
			"double right click", "POST", "/computeruse/mouse/click", `{"x":12,"y":24,"button":"right","double":true}`, response,
			func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Mouse().Click(ctx, point, &ComputerUseClickOptions{Button: ComputerUseMouseButtonRight, Double: true})
				return err
			},
		},
		{
			"drag", "POST", "/computeruse/mouse/drag", `{"startX":0,"startY":0,"endX":12,"endY":24,"button":"middle"}`, response,
			func(ctx context.Context, c *ComputerUse) error {
				_, err := c.Mouse().Drag(ctx, ComputerUseDrag{End: point}, &ComputerUseDragOptions{Button: ComputerUseMouseButtonMiddle})
				return err
			},
		},
		{
			"scroll default", "POST", "/computeruse/mouse/scroll", `{"x":12,"y":24,"direction":"down"}`, `{"success":true}`,
			func(ctx context.Context, c *ComputerUse) error {
				got, err := c.Mouse().Scroll(ctx, ComputerUseScroll{Position: point, Direction: ComputerUseScrollDown}, nil)
				if err == nil {
					require.True(t, got.Success)
				}
				return err
			},
		},
		{
			"scroll zero", "POST", "/computeruse/mouse/scroll", `{"x":0,"y":0,"direction":"up","amount":0}`, `{"success":false}`,
			func(ctx context.Context, c *ComputerUse) error {
				amount := 0
				got, err := c.Mouse().Scroll(ctx, ComputerUseScroll{Direction: ComputerUseScrollUp}, &ComputerUseScrollOptions{Amount: &amount})
				if err == nil {
					require.False(t, got.Success)
				}
				return err
			},
		},
	})
}

func TestComputerUseKeyboard_whenInputSent(t *testing.T) {
	runDesktopWireCases(t, []desktopWireCase{
		{
			"type default", "POST", "/computeruse/keyboard/type", `{"text":"hello"}`, `{}`,
			func(ctx context.Context, c *ComputerUse) error { return c.Keyboard().Type(ctx, "hello", nil) },
		},
		{
			"type delay rounded", "POST", "/computeruse/keyboard/type", `{"text":"hello","delay":2}`, `{}`,
			func(ctx context.Context, c *ComputerUse) error {
				return c.Keyboard().Type(ctx, "hello", &ComputerUseTypeOptions{Delay: 1500 * time.Microsecond})
			},
		},
		{
			"key defaults", "POST", "/computeruse/keyboard/key", `{"key":"Return"}`, `{}`,
			func(ctx context.Context, c *ComputerUse) error { return c.Keyboard().Key(ctx, "Return", nil) },
		},
		{
			"key modifiers", "POST", "/computeruse/keyboard/key", `{"key":"a","modifiers":["ctrl","shift"]}`, `{}`,
			func(ctx context.Context, c *ComputerUse) error {
				return c.Keyboard().Key(ctx, "a", &ComputerUseKeyOptions{Modifiers: []ComputerUseKeyboardModifier{
					ComputerUseModifierCtrl, ComputerUseModifierShift,
				}})
			},
		},
		{
			"empty modifiers", "POST", "/computeruse/keyboard/key", `{"key":"a","modifiers":[]}`, `{}`,
			func(ctx context.Context, c *ComputerUse) error {
				return c.Keyboard().Key(ctx, "a", &ComputerUseKeyOptions{Modifiers: []ComputerUseKeyboardModifier{}})
			},
		},
		{
			"hotkey", "POST", "/computeruse/keyboard/hotkey", `{"keys":"ctrl+c"}`, `{}`,
			func(ctx context.Context, c *ComputerUse) error { return c.Keyboard().Hotkey(ctx, "ctrl+c") },
		},
	})
}
