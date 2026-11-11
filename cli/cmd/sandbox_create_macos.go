// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

func newSandboxCreateMacosCommand() *cobra.Command {
	return newCreateSurfaceCommand(createSurface{
		use: "create-macos", short: "Create a macOS sandbox on a reserved Mac",
		long: "Create a macOS sandbox on a reserved Mac (POST /sandboxes/macos). The SDK twin is create_macos(image, …).\n" +
			"Each omitted size uses half of the Mac's resources, rounded down, on a model that runs two sandboxes, or all of them on a model that runs one.\n" +
			"Stop and pause need --persistent. Stop keeps the disk on the same Mac, and start and resume use that Mac. Delete erases the disk. At reservation end, the disk is saved as an anonymous image before the Mac is erased; a lost Mac saves it if it comes back within 7 days. Create a sandbox from an image to continue on another Mac.\n" +
			"Examples:\n" +
			"  daytona sandbox create-macos --image base-macos\n" +
			"  daytona sandbox create-macos --image base-macos --reservation RESERVATION_ID --cores 10 --memory 16384 --disk 163840 --persistent\n" +
			"  daytona sandbox create-macos --image base-macos --block-all --allow-domain example.com",
		path: "/sandboxes/macos", secrets: true, macos: true, volumes: true, networkPolicy: true,
		decode: decodeAndCreateMacos,
		keys:   input.CheckKeysFor[gen.CreateMacosSandboxRequest],
	})
}

func decodeAndCreateMacos(cmd *cobra.Command, a *app.App, _ sandboxCreateFlags, body input.Body, headers http.Header) error {
	req, err := workload.DecodeBody[gen.CreateMacosSandboxRequest](body)
	if err != nil {
		return err
	}
	if err := checkMacosCreate(req); err != nil {
		return err
	}
	if req.Reservation == nil || *req.Reservation == "" || *req.Reservation == "new" {
		if err := a.Confirm("buy", "a 24-hour Mac reservation billed to the organization if needed to create this sandbox"); err != nil {
			return err
		}
	}
	return postCreate(cmd, a, req, headers)
}

// checkMacosCreate refuses, before the purchase confirmation and any request,
// what the API would refuse in a macOS create (flags and --file alike).
func checkMacosCreate(req gen.CreateMacosSandboxRequest) error {
	minimums := []struct {
		value *int32
		flag  string
		min   int32
	}{{req.Cores, "--cores (cores)", 4}, {req.MemoryMib, "--memory (memory_mib)", 4096}, {req.DiskMib, "--disk (disk_mib)", 81920}}
	for _, m := range minimums {
		if m.value != nil && *m.value < m.min {
			return cerr.Usage("%s must be at least %d, got %d", m.flag, m.min, *m.value)
		}
	}
	if req.AutoRenew != nil && (req.Reservation == nil || *req.Reservation != "new") {
		return cerr.Usage("--auto-renew (auto_renew) applies only with --reservation new")
	}
	if req.Volumes == nil {
		return nil
	}
	volumes := *req.Volumes
	if len(volumes) > 8 {
		return cerr.Usage("a macOS sandbox mounts at most 8 volumes, got %d", len(volumes))
	}
	for _, v := range volumes {
		if v.ReadOnly != nil && *v.ReadOnly {
			return cerr.Usage("volume %q: a macOS sandbox cannot mount a volume read-only", v.Volume)
		}
	}
	if len(volumes) > 0 && req.Persistent != nil && *req.Persistent {
		return cerr.Usage("a macOS sandbox with volumes cannot be persistent")
	}
	return nil
}

func macosCreateLifecycleFlags(f *pflag.FlagSet, flags *sandboxCreateFlags) {
	f.StringVar(&flags.onLifecycleTTL, "on-lifecycle-timeout", "", "action when --lifecycle-timeout elapses: delete, or stop with --persistent")
	f.StringVar(&flags.onIdleTimeout, "on-idle-timeout", "", "action when --idle-timeout elapses: delete, or stop with --persistent")
	flagPlaceholder(f, "on-lifecycle-timeout", "ACTION")
	flagPlaceholder(f, "on-idle-timeout", "ACTION")
}

func macosCreateFlags(f *pflag.FlagSet, flags *sandboxCreateFlags) {
	f.Int32Var(&flags.cores, "cores", 0, "CPU cores, minimum 4; a Mac core is one CPU of the Mac (default: half of the Mac, rounded down, on a model that runs two sandboxes; all of it on a model that runs one)")
	f.Int32Var(&flags.memory, "memory", 0, "memory in MiB, minimum 4096 (default: half of the Mac, rounded down, on a model that runs two sandboxes; all of it on a model that runs one)")
	f.Int32Var(&flags.disk, "disk", 0, "whole sandbox disk in MiB, macOS included, minimum 81920 (default: half of the Mac's sandbox disk capacity, rounded down to a whole GiB, on a model that runs two sandboxes; all of it on a model that runs one)")
	f.StringArrayVar(&flags.env, "env", nil, "environment variable KEY=VALUE (repeatable)")
	f.BoolVar(&flags.persistent, "persistent", false, "keep the disk across stop/start on the same Mac; delete erases it, and reservation end saves it as an image before the Mac is erased. If a Mac restarts before it is declared lost, a running persistent sandbox comes back stopped with its disk, a running ephemeral sandbox is lost, and stopped and paused sandboxes are unchanged.")
	f.StringVar(&flags.reservation, "reservation", "", "target an existing reservation by ID; new buys the cheapest fitting model; omitted may buy if none fits (confirmation required unless --yes)")
	f.BoolVar(&flags.autoRenew, "auto-renew", false, autoRenewUsage)
	flagPlaceholder(f, "cores", "N")
	flagPlaceholder(f, "memory", "MIB")
	flagPlaceholder(f, "disk", "MIB")
	flagPlaceholder(f, "reservation", "ID|new")
}
