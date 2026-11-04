// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

func newSandboxCreateMacosCommand() *cobra.Command {
	return newCreateSurfaceCommand(createSurface{
		use: "create-macos", short: "Create a macOS sandbox on a reserved Mac",
		long: "Create a macOS sandbox on a reserved Mac (POST /sandboxes/macos). The SDK twin is create_macos(image, …).\n" +
			"Each omitted size uses the Mac model's default size (DEFAULT_SIZE in daytona mac-model list). A create from a captured image gets the image's disk size when --disk is omitted, and refuses a smaller --disk. The sizes of the sandboxes on one Mac must fit that Mac.\n" +
			"Stop and pause need --persistent. Keep the sandbox's disk and machine across stop and start, and its memory across pause and resume. A start can run on any Mac of the organization. After a start on a different Mac, iCloud asks the user to sign in again. A reservation end or a Mac failure stops a sandbox that has started and never deletes it. Delete erases the disk.\n" +
			"Examples:\n" +
			"  daytona sandbox create-macos --image base-macos\n" +
			"  daytona sandbox create-macos --image base-macos --reservation RESERVATION_ID --cores 10 --memory 16384 --disk 163840 --persistent\n" +
			"  daytona sandbox create-macos --image base-macos --block-all --allow-domain example.com",
		path: "/sandboxes/macos", secrets: true, macos: true, volumes: true, networkPolicy: true,
		decode: decodeAndCreateMacos,
	})
}

func decodeAndCreateMacos(cmd *cobra.Command, a *app.App, _ sandboxCreateFlags, body input.Body, headers http.Header) error {
	req, err := workload.DecodeBody[gen.CreateMacosSandboxRequest](body)
	if err != nil {
		return err
	}
	if req.Reservation == nil || *req.Reservation == "" || *req.Reservation == "new" {
		if err := a.Confirm("buy", "a 24-hour Mac reservation billed to the organization if needed to create this sandbox"); err != nil {
			return err
		}
	}
	return postCreate(cmd, a, req, headers)
}

func macosCreateLifecycleFlags(f *pflag.FlagSet, flags *sandboxCreateFlags) {
	f.StringVar(&flags.onTimeout, "on-timeout", "", "action when --timeout elapses: stop|delete (stop needs --persistent)")
	f.StringVar(&flags.onIdleTimeout, "on-idle-timeout", "", "action when --idle-timeout elapses: stop|delete (stop needs --persistent)")
	flagPlaceholder(f, "on-timeout", "ACTION")
	flagPlaceholder(f, "on-idle-timeout", "ACTION")
}

func macosCreateFlags(f *pflag.FlagSet, flags *sandboxCreateFlags) {
	f.Int32Var(&flags.cores, "cores", 0, "CPU cores, at least 4; a Mac core is one CPU of the Mac (default: the Mac model's default size)")
	f.Int32Var(&flags.memory, "memory", 0, "memory in MiB, at least 4096 (default: the Mac model's default size)")
	f.Int32Var(&flags.disk, "disk", 0, "disk size in MiB, macOS included, at least 81920 (default: the Mac model's default size, or a captured image's disk size)")
	f.StringArrayVar(&flags.env, "env", nil, "environment variable KEY=VALUE (repeatable)")
	f.BoolVar(&flags.persistent, "persistent", false, "keep the sandbox's disk and machine across stop and start, and its memory across pause and resume. A start can run on any Mac of the organization. After a start on a different Mac, iCloud asks the user to sign in again. A reservation end or a Mac failure stops a sandbox that has started and never deletes it.")
	f.StringVar(&flags.reservation, "reservation", "", "a reservation ID, or new to buy the cheapest model that fits; omitted uses any reservation with space, and buys one under auto-reserve only when the organization has none in the region (confirmation required unless --yes)")
	f.BoolVar(&flags.autoRenew, "auto-renew", false, autoRenewUsage)
	flagPlaceholder(f, "cores", "N")
	flagPlaceholder(f, "memory", "MIB")
	flagPlaceholder(f, "disk", "MIB")
	flagPlaceholder(f, "reservation", "ID|new")
}
