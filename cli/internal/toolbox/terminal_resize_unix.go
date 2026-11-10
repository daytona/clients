// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

//go:build !windows

package toolbox

import (
	"context"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func (c *Client) forwardTerminalResizes(ctx context.Context, processID string) func() {
	resizeCtx, cancel := context.WithCancel(ctx)
	changes := make(chan os.Signal, 1)
	signal.Notify(changes, syscall.SIGWINCH)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			select {
			case <-resizeCtx.Done():
				return
			case <-changes:
				requestCtx, stop := context.WithTimeout(resizeCtx, 3*time.Second)
				err := c.ResizeProcess(requestCtx, processID, terminalSize(int(os.Stdout.Fd())))
				stop()
				if err != nil {
					continue // best effort: the PTY may have exited during the resize
				}
			}
		}
	}()
	return func() {
		signal.Stop(changes)
		cancel()
		<-done
	}
}
