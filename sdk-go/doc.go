// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Package daytona is the Go SDK for Daytona sandboxes: create sandboxes, run
// commands and code in them, move files, use git, expose ports, and build
// reusable images.
//
//	client, err := daytona.New() // reads DAYTONA_API_KEY
//	if err != nil {
//		return err
//	}
//	defer client.Close()
//
//	sandbox, err := client.Create(ctx, "", nil) // "" = the default image, with Python 3
//	if sandbox != nil {
//		defer sandbox.Delete(context.WithoutCancel(ctx), nil)
//	}
//	if err != nil {
//		return err
//	}
//
//	result, err := sandbox.Exec(ctx, `python3 -c "print(6 * 7)"`, nil)
//	if err != nil {
//		return err // the call failed; a non-zero exit code is not an error
//	}
//	fmt.Println(result.Reason, result.Stdout)
//
// # Where things are
//
//   - Sandboxes: [Client.Create], [Client.CreateFromBuild], [Client.Get],
//     [Client.List], [Client.FindByName],
//     [Sandbox.Delete], [Sandbox.Start], [Sandbox.Stop], [Sandbox.Fork].
//     A *Sandbox keeps its last observation: [Sandbox.ID], [Sandbox.State],
//     [Sandbox.Info]; [Sandbox.Refresh] re-reads it. A *ProcessHandle keeps
//     its identity ([ProcessHandle.ID], [ProcessHandle.Kind]);
//     [ProcessHandle.Get] reads the process.
//   - Commands and code: [Sandbox.Exec] (shell), [Sandbox.ExecArgs] (argv),
//     [Sandbox.RunCode] (stateful Python), [Sandbox.Process] returns
//     [*Processes] (background processes, sessions), [Sandbox.PTY] returns
//     [*PTY] (terminals).
//   - Files: [Sandbox.FS] returns [*FileSystem] — [FileSystem.WriteFile],
//     [FileSystem.ReadFile], [FileSystem.UploadFile], [FileSystem.DownloadFile],
//     Upload/Download for streams, List, Watch.
//   - Git: [Sandbox.Git] returns [*Git] — Clone, CreateBranch, CheckoutBranch,
//     Add, Commit (author name and email are required), Push, History.
//   - Ports and URLs: [Sandbox.Ports] returns [*Ports] — [Ports.Expose] with
//     Public: true gives a URL that opens without credentials; PreviewLink
//     preserves the endpoint's visibility. SignedPreviewLink gives a private
//     token-bearing URL, with its lifetime in [SignedPreviewLinkOptions].ExpiresIn
//     (nil options or zero uses the server default).
//   - Images: [Client.Images] returns [*Images] — [Images.Pull] imports a
//     registry image, [Images.BuildAndWait] builds one from an [ImageBuilder];
//     create sandboxes from the image's name or ID, or use [Client.CreateFromBuild]
//     to build and boot in one call. [Client.Snapshots],
//     [Client.Volumes], [Client.Secrets] manage the other project resources.
//
// A registry reference such as "python:3.12" is not a [Client.Create] source:
// import it once with [Images.Pull], then create from the returned image.
//
// Paths inside the sandbox are absolute ("/workspace/data.csv"); file
// operations refuse relative paths, and uploads create missing parent
// directories. Commands without [ExecOptions].Cwd start in the user's home
// directory when it is available (not with an explicit [ExecOptions].User),
// otherwise in the sandbox's working directory; `pwd` prints it.
//
// # Calls, options and durations
//
// Every call that talks to Daytona takes a [context.Context] first; cancelling
// it stops the call, including waits. Optional settings are an options struct
// pointer last, and nil means all defaults. Every duration is a
// [time.Duration]. Sandbox creates and lifecycle calls (start, stop, delete)
// wait 60 seconds by default; image readiness waits ([Images.Pull],
// [Images.BuildAndWait], the build in [Client.CreateFromBuild]) have no SDK
// deadline unless their timeout is set. [NoWait] returns once the server
// accepted the request, and [WaitForever] removes the SDK's deadline.
// Once a create or fork is accepted, its handle is returned even if the caller's
// context ends the wait with an error. Delete the non-nil handle or keep it;
// cleanup needs a context that has not been cancelled.
//
// # Deleting resources
//
// [Sandbox.Delete] waits until the sandbox is destroyed. The platform releases
// what the sandbox used (its image, attached volumes) a few seconds later, so
// deleting the image or a volume right after its sandbox can return
// [ErrConflict] for a short while; retry it after a moment. Delete sandboxes
// before the images and volumes they use.
//
// # Errors
//
// Every error an SDK call returns is, or wraps, an [*Error]. Match a category
// with errors.Is(err, [ErrNotFound]) or a specific code with
// errors.Is(err, [ErrSandboxNotRunning]), and read the details with errors.As.
// Context cancellation stays reachable through errors.Is. Streams that
// implement io.Reader follow its contract instead: [PTYConnection.Read]
// returns io.EOF, unwrapped, when the terminal closes normally.
//
// # Configuration
//
// [New] reads DAYTONA_API_KEY, or DAYTONA_JWT_TOKEN with DAYTONA_PROJECT_ID;
// options such as [WithAPIKey] take precedence. A credential must be printable
// ASCII with no leading or trailing whitespace (environment values are trimmed
// first); [New] returns [ErrBadRequest] otherwise. DAYTONA_V2_API_URL selects a
// different API endpoint. A [Client] is safe for concurrent use; close it when
// done.
package daytona
