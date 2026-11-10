// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"fmt"
	"net/http"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/regions"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

type sandboxCreateFlags struct {
	name           string
	image          string
	snapshot       string
	cpu            float64
	cores          int32
	memory         int32
	disk           int32
	cpuMax         float64
	memoryMax      int32
	diskMax        int32
	cpuArch        string
	cpuType        string
	idempotencyKey string
	user           string
	env            []string
	labels         []string
	ports          []string
	volumes        []string
	secrets        []string
	persistent     bool
	spot           bool
	allowDomains   []string
	allowCIDRs     []string
	blockAll       bool
	lifecycleTTL   secondsFlag
	onLifecycleTTL string
	idleTimeout    secondsFlag
	onIdleTimeout  string
	wait           secondsFlag
	createTTL      secondsFlag
	reservation    string
	autoRenew      bool
}

// One create command per API create surface, mirroring the SDKs 1:1.
// Each command registers only the flags its request type supports.
//
//	sandbox create                       POST /sandboxes                   CreateSandboxRequest
//	sandbox create-from-snapshot         POST /sandboxes/from-snapshot     CreateSandboxFromSnapshotRequest
//	sandbox create-macos                 POST /sandboxes/macos             CreateMacosSandboxRequest
type createSurface struct {
	use, short, long, path string
	// snapshot: the positional argument is a memory snapshot (no --image).
	snapshot bool
	// imageOptional: --image may be omitted; the server boots the platform
	// default image (Linux only).
	imageOptional bool
	// flag groups; macos is the macOS set: sizes, env, persistent, the reservation
	// selectors and the stop|delete lifecycle actions.
	resources, burst, cpuConstraints, user, volumes, secrets, spot, networkPolicy, macos bool
	decode                                                                               func(cmd *cobra.Command, a *app.App, flags sandboxCreateFlags, body input.Body, headers http.Header) error
	// keys checks the body's keys against the request type, before the
	// lifecycle check reads them.
	keys func(input.Body) error
}

func newSandboxCreateCommand() *cobra.Command {
	return newCreateSurfaceCommand(createSurface{
		use: "create", short: "Create a sandbox from an image",
		long: "Create a Linux sandbox from an image (POST /sandboxes). The SDK twin is create([image], …).\nWithout --image the sandbox boots the platform default image.\nExamples:\n  daytona sandbox create\n  daytona sandbox create --image py312\n  daytona sandbox create --image py312 --name example-user --server-wait 60s --create-ttl 1h\n  daytona sandbox create --image py312 --lifecycle-timeout 2h --on-lifecycle-timeout delete",
		path: "/sandboxes", imageOptional: true, resources: true, burst: true, cpuConstraints: true, user: true, volumes: true, secrets: true, spot: true, networkPolicy: true,
		decode: decodeAndCreate[gen.CreateSandboxRequest],
		keys:   input.CheckKeysFor[gen.CreateSandboxRequest],
	})
}

func newSandboxCreateFromSnapshotCommand() *cobra.Command {
	return newCreateSurfaceCommand(createSurface{
		use: "create-from-snapshot <snapshot-name-or-snap_id>", short: "Create a sandbox from a snapshot",
		long: "Create a Linux sandbox from a snapshot (POST /sandboxes/from-snapshot). The SDK twin is create_from_snapshot(snapshot, …).\nResources come from the snapshot; only name, labels, ports, lifecycle, wait and create-ttl apply.\nExample:\n  daytona sandbox create-from-snapshot after-setup --name resumed",
		path: "/sandboxes/from-snapshot", snapshot: true,
		decode: decodeAndCreate[gen.CreateSandboxFromSnapshotRequest],
		keys:   input.CheckKeysFor[gen.CreateSandboxFromSnapshotRequest],
	})
}

func decodeAndCreate[T any](cmd *cobra.Command, a *app.App, flags sandboxCreateFlags, body input.Body, headers http.Header) error {
	req, err := workload.DecodeBody[T](body)
	if err != nil {
		return err
	}
	return postCreate(cmd, a, req, headers)
}

func postCreate(cmd *cobra.Command, a *app.App, req any, headers http.Header) error {
	path := cmd.Annotations["path"]
	home, _, err := a.WorkloadClient(cmd.Context(), pickProject(a))
	if err != nil {
		return err
	}
	var created api.Raw[gen.SandboxCreateOperation]
	if err := home.DoWithHeaders(cmd.Context(), http.MethodPost, path, nil, headers, req, &created); err != nil {
		return withRetryKeyHint(err, headers.Get("Idempotency-Key"))
	}
	return printCreatedSandbox(cmd, a, home, workload.NewCatalogCache(), created)
}

func newCreateSurfaceCommand(sf createSurface) *cobra.Command {
	flags := &sandboxCreateFlags{}
	// Snapshot surfaces take the snapshot as their one positional argument;
	// image surfaces take none (the image is --image). Cobra enforces the
	// count before RunE, so args[0] below is always present.
	args := cobra.NoArgs
	if sf.snapshot {
		args = func(_ *cobra.Command, args []string) error {
			if len(args) != 1 {
				return cerr.Usage("expected exactly one <snapshot-name-or-snap_id> argument, got %d", len(args))
			}
			return nil
		}
	}
	cmd := &cobra.Command{
		Use:         sf.use,
		Short:       sf.short,
		Long:        sf.long,
		Args:        args,
		Annotations: map[string]string{"path": sf.path},
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			if sf.snapshot {
				flags.snapshot = args[0]
			}
			body, err := buildSandboxCreateBody(cmd, a, *flags, sf.snapshot, sf.imageOptional)
			if err != nil {
				return err
			}
			if err := sf.keys(body); err != nil {
				return err
			}
			if err := validateLifecycleBody(sf.macos, body); err != nil {
				return err
			}
			headers, err := sandboxCreateHeaders(*flags, cmd, a.Globals.Region, body)
			if err != nil {
				return err
			}
			return sf.decode(cmd, a, *flags, body, headers)
		},
	}
	if sf.imageOptional {
		cmd.Flags().StringVar(&flags.image, "image", "", "image name or img_… id (default: the platform default image)")
	} else if !sf.snapshot {
		cmd.Flags().StringVar(&flags.image, "image", "", "image name or img_… id (required)")
	}
	f := cmd.Flags()
	f.StringVar(&flags.name, "name", "", "sandbox name")
	f.StringVar(&flags.idempotencyKey, "idempotency-key", "", "idempotency key")
	f.StringArrayVar(&flags.labels, "label", nil, "label KEY=VALUE (repeatable)")
	f.StringArrayVar(&flags.ports, "port", nil, "port as PORT or PORT/PROTO (repeatable)")
	addSecondsFlag(f, &flags.lifecycleTTL, "lifecycle-timeout", "", "wall-clock lifetime (Go duration, e.g. 2h) until --on-lifecycle-timeout fires; re-arms on every start")
	flags.idleTimeout.min = minIdleSeconds
	addSecondsFlag(f, &flags.idleTimeout, "idle-timeout", "", "idle time (Go duration, at least 1m) until --on-idle-timeout fires")
	if sf.macos {
		macosCreateLifecycleFlags(f, flags)
	} else {
		f.StringVar(&flags.onLifecycleTTL, "on-lifecycle-timeout", "", "action when --lifecycle-timeout elapses: delete")
		f.StringVar(&flags.onIdleTimeout, "on-idle-timeout", "", "action when --idle-timeout elapses: delete")
		flagPlaceholder(f, "on-lifecycle-timeout", "ACTION")
		flagPlaceholder(f, "on-idle-timeout", "ACTION")
	}
	flags.wait.allowZero = true
	addSecondsFlag(f, &flags.wait, "server-wait", "", "how long the server holds the create response for the sandbox to reach running (Go duration; 0s answers at once; server default 3s); the CLI still waits for running unless --no-wait")
	flags.createTTL.keywords = map[string]int64{"max": -1}
	addSecondsFlag(f, &flags.createTTL, "create-ttl", "", "deadline (Go duration) for the sandbox to reach running, queued or booting, before the server deletes it; max = the server's maximum (server default 1h)")
	if sf.macos {
		macosCreateFlags(f, flags)
	}
	if sf.resources {
		f.Float64Var(&flags.cpu, "cpu", 0, "guaranteed CPU in cores (1 core = 2 vCPUs)")
		f.Int32Var(&flags.memory, "memory", 0, "memory in MiB")
		f.Int32Var(&flags.disk, "disk", 0, "scratch disk in MiB")
		f.StringArrayVar(&flags.env, "env", nil, "environment variable KEY=VALUE (repeatable)")
	}
	if sf.burst {
		f.Float64Var(&flags.cpuMax, "cpu-max", 0, "CPU limit in cores, a multiple of 0.5 (boot shape and CPU ceiling); equal to --cpu for a fixed shape")
		f.Int32Var(&flags.memoryMax, "memory-max", 0, "memory limit in MiB; equal to --memory for a fixed shape")
		f.Int32Var(&flags.diskMax, "disk-max", 0, "scratch disk limit in MiB; equal to --disk for a fixed shape")
	}
	if sf.cpuConstraints {
		f.StringVar(&flags.cpuArch, "cpu-arch", "", "CPU architecture constraint")
		f.StringVar(&flags.cpuType, "cpu-type", "", "CPU type constraint")
	}
	if sf.user {
		f.StringVar(&flags.user, "user", "", "default sandbox OS user")
	}
	if sf.volumes && sf.macos {
		f.StringArrayVar(&flags.volumes, "volume", nil, "disk volume mount VOLUME:MOUNT_PATH[:SUBPATH] (repeatable; read-write, a macOS sandbox cannot mount a volume read-only)")
	} else if sf.volumes {
		f.StringArrayVar(&flags.volumes, "volume", nil, "volume mount VOLUME:MOUNT_PATH[:SUBPATH][:ro] (repeatable; a trailing :ro mounts read-only)")
	}
	if sf.secrets {
		f.StringArrayVar(&flags.secrets, "secret", nil, "secret name to attach (repeatable)")
	}
	if sf.spot {
		f.BoolVar(&flags.spot, "spot", false, "use the spot tier")
	}
	if sf.networkPolicy {
		f.StringArrayVar(&flags.allowDomains, "allow-domain", nil, "network policy: allowed hostname or *.wildcard (repeatable; implies --block-all)")
		f.StringArrayVar(&flags.allowCIDRs, "allow-cidr", nil, "network policy: allowed IPv4 address or CIDR, any port (repeatable; needs --block-all or --allow-domain)")
		f.BoolVar(&flags.blockAll, "block-all", false, "network policy: deny all egress not explicitly allowed")
	}
	addRegionFlag(cmd, "region to create the sandbox in (default: the project's, then the organization's, then earth)")
	addFileFlag(cmd, "")
	addWaitFlags(cmd)
	return cmd
}

func printCreatedSandbox(cmd *cobra.Command, a *app.App, home *api.Client, cache *workload.CatalogCache, raw api.Raw[gen.SandboxCreateOperation]) error {
	created := raw.V
	// The create acknowledgement carries the sandbox's access token: it is
	// never printed. Both modes print the sandbox detail, the shape `sandbox
	// get` prints: --no-wait reads it once, the default waits for running.
	if a.Globals.NoWait {
		resolved, err := workload.ResolveSandbox(cmd.Context(), home, cache, created.Id)
		if err != nil {
			// The create succeeded: print what the acknowledgement says that
			// is also a detail field (never its access token), and warn.
			a.Info("warning: sandbox %s was created, but reading it failed: %v", created.Id, err)
			return a.Printer().Object(map[string]any{"id": created.Id, "name": created.Name, "status": created.Status},
				[][2]string{{"id", created.Id}, {"name", created.Name}, {"status", created.Status}})
		}
		return a.Printer().Object(resolved.Raw, sandboxDetailKV(resolved.Detail))
	}
	timeout, err := workload.ParseTimeout(a.Globals.Timeout)
	if err != nil {
		return err
	}
	cat, err := cache.Fetch(cmd.Context(), home)
	if err != nil {
		return createdButUnreadable(created.Id, err)
	}
	client := regions.ClientFor(cmd.Context(), home, cat, created.Id)
	detail, err := workload.WaitForSandbox(cmd.Context(), client, workload.SandboxWaitOptions{
		ID:      created.Id,
		Target:  "running",
		Timeout: timeout,
	})
	if err != nil {
		return createdButUnreadable(created.Id, err)
	}
	return a.Printer().Object(detail, sandboxDetailKV(detail.V))
}

// withRetryKeyHint names the idempotency key when a create may have reached
// the server without an answer (a network error, a 5xx, a timeout), so a retry
// with the same key replays it instead of creating a second sandbox.
func withRetryKeyHint(err error, key string) error {
	if code := cerr.ExitCode(err); key == "" || (code != cerr.ExitFailure && code != cerr.ExitTimeout) {
		return err
	}
	out := &cerr.Error{Code: cerr.ExitCode(err), Err: err}
	var ce *cerr.Error
	out.Hint = fmt.Sprintf("the sandbox may have been created; retry with --idempotency-key %s to get the same sandbox instead of a second one", key)
	if errors.As(err, &ce) {
		out.Source = ce.Source
		if ce.Hint != "" {
			out.Hint = ce.Hint + "; " + out.Hint
		}
	}
	return out
}

// createdButUnreadable keeps the exit code and details of err but names the
// sandbox the accepted create made, so a failed read or wait never leaves the
// caller without its id.
func createdButUnreadable(id string, err error) error {
	out := &cerr.Error{Code: cerr.ExitCode(err), Msg: fmt.Sprintf("sandbox %s was created, but %v", id, err), Err: err}
	var ce *cerr.Error
	if errors.As(err, &ce) {
		out.Hint, out.Source = ce.Hint, ce.Source
	}
	if out.Hint == "" {
		out.Hint = fmt.Sprintf("check it with `daytona sandbox get %s`; delete it with `daytona sandbox delete %s`", id, id)
	}
	return out
}
