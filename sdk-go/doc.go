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
//   - Sandboxes: [Client.Create], [Client.Get], [Client.List], [Client.FindByName],
//     [Sandbox.Delete], [Sandbox.Start], [Sandbox.Stop], [Sandbox.Fork].
//   - Commands and code: [Sandbox.Exec] (shell), [Sandbox.ExecArgs] (argv),
//     [Sandbox.RunCode] (stateful Python), [Sandbox.Process] (background
//     processes, sessions), [Sandbox.PTY] (terminals).
//   - Files: [Sandbox.FS] — WriteFile, ReadFile, UploadFile, DownloadFile,
//     Upload/Download for streams, List, Watch.
//   - Git: [Sandbox.Git] — Clone, CreateBranch, CheckoutBranch, Add, Commit
//     (author name and email are required), Push, History.
//   - Ports and URLs: [Sandbox.Ports] — Expose with Public: true gives a URL
//     that opens without credentials; PreviewLink and SignedPreviewLink give
//     private ones.
//   - Images: [Client.Images] — Pull imports a registry image, BuildAndWait
//     builds one from an [ImageBuilder]; create sandboxes from the image's name
//     or ID. [Client.Snapshots], [Client.Volumes], [Client.Secrets] manage the
//     other project resources.
//
// A registry reference such as "python:3.12" is not a [Client.Create] source:
// import it once with [Images.Pull], then create from the returned image.
//
// # Calls, options and durations
//
// Every call that talks to Daytona takes a [context.Context] first; cancelling
// it stops the call, including waits. Optional settings are an options struct
// pointer last, and nil means all defaults. Every duration is a
// [time.Duration]: operations that wait (create, start, stop, delete, image
// builds) wait 60 seconds by default, [NoWait] returns once the server
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
// Every error the SDK returns is, or wraps, an [*Error]. Match a category
// with errors.Is(err, [ErrNotFound]) or a specific code with
// errors.Is(err, [ErrSandboxNotRunning]), and read the details with errors.As.
// Context cancellation stays reachable through errors.Is.
//
// # Configuration
//
// [New] reads DAYTONA_API_KEY, or DAYTONA_JWT_TOKEN with DAYTONA_PROJECT_ID;
// options such as [WithAPIKey] take precedence. DAYTONA_V2_API_URL selects a
// different API endpoint. A [Client] is safe for concurrent use; close it when
// done.
package daytona
