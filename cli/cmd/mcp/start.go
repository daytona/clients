// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package mcp

import (
	"os"
	"os/signal"

	"github.com/spf13/cobra"
	"go.daytona.io/cli/mcp"
)

var StartCmd = &cobra.Command{
	Use:   "start",
	Short: "Start Daytona MCP Server",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		server := mcp.NewDaytonaMCPServer()

		interruptChan := make(chan os.Signal, 1)
		signal.Notify(interruptChan, os.Interrupt)

		errChan := make(chan error)

		go func() {
			errChan <- server.Start()
		}()

		select {
		case err := <-errChan:
			return err
		case <-interruptChan:
			return nil
		}
	},
}
