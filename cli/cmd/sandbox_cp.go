// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

const presignedTransferTTL = time.Hour
const remoteArchiveCleanupTimeout = 10 * time.Second

func newSandboxCPSubcommand() *cobra.Command {
	var recursive bool
	cmd := &cobra.Command{
		Use:   "cp <src> <dst>",
		Short: "Copy files to or from a sandbox",
		Long: "Copies one file over a pre-signed toolbox URL minted by the API (POST /sandboxes/{id}/files/sign). A file copied onto an existing sandbox directory lands inside it, as with POSIX cp; a local directory needs -r. With -r, destination handling matches POSIX cp -r: if the destination ends with / (or is an existing local directory on download), the source directory is copied into it as <dest>/<basename(src)>; otherwise the destination path is the new directory itself. A file upload creates the missing parent directories of its remote destination, where the sandbox user may create them.\n" +
			"A local endpoint of - is stdin (upload) or stdout (download), for one file; a local file named - is ./-. An upload from stdin needs a remote file path, not a directory.\n" +
			"In JSON or YAML output (the default when stdout is not a terminal, or -o json|yaml) a finished copy prints {source, destination, bytes}, or {source, destination, recursive} for -r, where destination is the path the file or directory landed at; a download to stdout prints only the file. Table output (-o table, or the default at a terminal) prints no acknowledgement.\n" +
			"Examples:\n  daytona sandbox cp ./app.py <sandbox-id>:/home/daytona/app.py\n  daytona sandbox cp <sandbox-id>:/var/log/app.log - | tail\n  tar -cf - src | daytona sandbox cp - <sandbox-id>:/tmp/src.tar",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			a := FromContext(cmd.Context())
			src, srcRemote := toolboxclient.ParseTransferSpec(args[0])
			dst, dstRemote := toolboxclient.ParseTransferSpec(args[1])
			switch {
			case srcRemote && dstRemote:
				return cerr.Usage("sandbox cp supports exactly one remote endpoint")
			case !srcRemote && !dstRemote:
				return cerr.Usage("sandbox cp needs one endpoint in <sandbox-id>:/path form")
			}
			stdio := (dstRemote && args[0] == "-") || (srcRemote && args[1] == "-")
			if stdio && recursive {
				return cerr.Usage("- (stdin or stdout) copies one file; -r copies a directory").WithHint("drop -r, or name a local directory")
			}
			if dstRemote && args[0] == "-" && strings.HasSuffix(dst.Path, "/") {
				return cerr.Usage("an upload from stdin needs a remote file path, not the directory %s", dst.Path).WithHint("name the file, e.g. %s:%sfile", dst.SandboxID, dst.Path)
			}
			if dstRemote && args[0] != "-" {
				if err := checkLocalUploadSource(args[0], recursive); err != nil {
					return err
				}
			}
			if srcRemote {
				toolClient, target, _, signer, err := sandboxClients(cmd.Context(), a, src.SandboxID)
				if err != nil {
					return err
				}
				if recursive {
					// Resolved before the copy creates it: the directory the
					// tree lands in, as recursiveDownload resolves it.
					landed, err := recursiveDownloadPath(src.Path, args[1])
					if err != nil {
						return err
					}
					if err := recursiveDownload(cmd, toolClient, src.Path, args[1]); err != nil {
						return err
					}
					return printCopyAck(a, args[0], landed, -1)
				}
				signedURL, err := toolboxclient.SignFileURL(cmd.Context(), signer, string(target.Detail.Class), target.Detail.Id, http.MethodGet, src.Path, presignedTransferTTL)
				if err != nil {
					return err
				}
				if args[1] == "-" {
					_, err := toolClient.DownloadTo(cmd.Context(), signedURL, a.Stdout)
					return err
				}
				localPath := toolboxclient.SingleFileLocalDestination(args[1], src.Path)
				n, err := toolClient.DownloadFileCounted(cmd.Context(), signedURL, localPath)
				if err != nil {
					return err
				}
				return printCopyAck(a, args[0], localPath, n)
			}
			toolClient, target, _, signer, err := sandboxClients(cmd.Context(), a, dst.SandboxID)
			if err != nil {
				return err
			}
			if recursive {
				if err := recursiveUpload(cmd, toolClient, signer, target, args[0], dst.Path); err != nil {
					return err
				}
				// The directory the tree was extracted into.
				return printCopyAck(a, args[0], dst.SandboxID+":"+destinationUploadPath(args[0], dst.Path), -1)
			}
			remotePath := dst.Path
			if args[0] != "-" {
				remotePath = destinationUploadPath(args[0], dst.Path)
			}
			if remotePath == dst.Path {
				isDir, err := remoteIsDir(cmd.Context(), toolClient, dst.Path)
				if err != nil {
					return err
				}
				if isDir && args[0] == "-" {
					return cerr.Usage("an upload from stdin needs a remote file path; %s is a directory", dst.Path).WithHint("name the file, e.g. %s:%s/file", dst.SandboxID, strings.TrimSuffix(dst.Path, "/"))
				}
				if isDir {
					remotePath = path.Join(dst.Path, filepath.Base(args[0]))
				}
			}
			signedURL, err := toolboxclient.SignFileURL(cmd.Context(), signer, string(target.Detail.Class), target.Detail.Id, http.MethodPost, remotePath, presignedTransferTTL)
			if err != nil {
				return err
			}
			var n int64
			if args[0] == "-" {
				n, err = toolClient.UploadReaderCounted(cmd.Context(), signedURL, "stdin", remotePath, cpStdin)
			} else {
				n, err = toolClient.UploadFileCounted(cmd.Context(), signedURL, args[0], remotePath)
			}
			if err != nil {
				return err
			}
			return printCopyAck(a, args[0], dst.SandboxID+":"+remotePath, n)
		},
	}
	cmd.Flags().BoolVarP(&recursive, "recursive", "r", false, "copy a directory by uploading one tar archive, then extracting it remotely")
	return cmd
}

// cpStdin is the stdin `sandbox cp - …` uploads; tests replace it.
var cpStdin io.Reader = os.Stdin

// printCopyAck prints what a finished copy did in JSON or YAML output (a
// table-mode copy stays silent, as cp is). bytes < 0 marks a recursive copy,
// whose archive size is not the files' size.
func printCopyAck(a *app.App, source, destination string, bytes int64) error {
	if a.Format == output.FormatTable {
		return nil
	}
	ack := map[string]any{"source": source, "destination": destination}
	if bytes < 0 {
		ack["recursive"] = true
	} else {
		ack["bytes"] = bytes
	}
	return a.Printer().Object(ack, nil)
}

// checkLocalUploadSource refuses, before anything is signed or sent, a local
// source that does not exist or does not match -r (exit 2).
func checkLocalUploadSource(localPath string, recursive bool) error {
	info, err := os.Stat(localPath)
	switch {
	case errors.Is(err, os.ErrNotExist):
		return cerr.Usage("%s: no such file or directory", localPath)
	case err != nil:
		return cerr.Usage("%s: %v", localPath, errors.Unwrap(err))
	case info.IsDir() && !recursive:
		return cerr.Usage("%s is a directory", localPath).WithHint("pass -r to copy a directory")
	case !info.IsDir() && recursive:
		return cerr.Usage("%s is not a directory; -r copies a directory", localPath).WithHint("drop -r to copy one file")
	}
	return nil
}

func recursiveDownload(cmd *cobra.Command, toolClient *toolboxclient.Client, remotePath, localPath string) error {
	targetPath, err := recursiveDownloadPath(remotePath, localPath)
	if err != nil {
		return err
	}
	pr, pw := io.Pipe()
	stderr := &bytes.Buffer{}
	errCh := make(chan error, 1)
	// A failed extraction must unblock the producer: closing the read end makes
	// every pending pw.Write return io.ErrClosedPipe, and cancelling the
	// context stops the remote stream, so the goroutine always exits.
	ctx, cancel := context.WithCancel(cmd.Context())
	defer cancel()
	go func() {
		result, err := toolClient.StreamProcess(ctx, toolboxclient.StreamProcessOptions{
			Request: tbgen.CreateProcessRequest{
				Argv:  &[]string{"tar", "-C", remotePath, "-cf", "-", "."},
				Stdin: stdinModePtr(tbgen.Closed),
			},
			Stdout:      pw,
			Stderr:      stderr,
			LogEncoding: "base64",
		})
		if err != nil {
			_ = pw.CloseWithError(err)
			errCh <- err
			return
		}
		exitErr := exitError(result.Exit)
		if exitErr != nil {
			_ = pw.CloseWithError(exitErr)
			errCh <- withRemoteStderr(exitErr, stderr.String())
			return
		}
		_ = pw.Close()
		errCh <- nil
	}()
	if err := toolboxclient.ExtractArchive(pr, targetPath); err != nil {
		_ = pr.CloseWithError(err)
		cancel()
		<-errCh // the producer has observed the closed pipe / cancelled ctx
		return err
	}
	// tar stops reading at the end-of-archive marker, but GNU tar pads its
	// output to a 10 KiB record boundary: those trailing zero blocks are still
	// on their way through pw.Write, which blocks on an unbuffered pipe until
	// someone reads. Drain, or the producer never reaches its exit result.
	_, _ = io.Copy(io.Discard, pr)
	return <-errCh
}

var newSandboxArchiveTempPath = func() (string, error) {
	raw := make([]byte, 8)
	if _, err := rand.Read(raw); err != nil {
		return "", fmt.Errorf("generate sandbox temp archive name: %w", err)
	}
	return "/tmp/.daytona-cp-" + hex.EncodeToString(raw) + ".tar", nil
}

func recursiveUpload(cmd *cobra.Command, toolClient *toolboxclient.Client, signer *api.Client, target *toolboxclient.Target, localPath, remotePath string) error {
	archive, err := toolboxclient.ArchiveDirectory(localPath)
	if err != nil {
		return err
	}
	defer archive.Close()
	remoteArchivePath, err := newSandboxArchiveTempPath()
	if err != nil {
		return err
	}
	signedURL, err := toolboxclient.SignFileURL(cmd.Context(), signer, string(target.Detail.Class), target.Detail.Id, http.MethodPost, remoteArchivePath, presignedTransferTTL)
	if err != nil {
		return err
	}
	if err := toolClient.UploadReader(cmd.Context(), signedURL, localPath, remoteArchivePath, archive); err != nil {
		bestEffortRemoteRemove(cmd.Context(), toolClient, remoteArchivePath)
		return err
	}
	extractPath := destinationUploadPath(localPath, remotePath)
	stderr := &bytes.Buffer{}
	result, err := toolClient.StreamProcess(cmd.Context(), toolboxclient.StreamProcessOptions{
		Request: tbgen.CreateProcessRequest{
			Argv:  &[]string{"sh", "-c", `mkdir -p "$2" && tar -xf "$1" -C "$2" && rm -f "$1"`, "sh", remoteArchivePath, extractPath},
			Stdin: stdinModePtr(tbgen.Closed),
		},
		Stdout:      io.Discard,
		Stderr:      stderr,
		LogEncoding: "text",
	})
	if err != nil {
		bestEffortRemoteRemove(cmd.Context(), toolClient, remoteArchivePath)
		return err
	}
	if exitErr := exitError(result.Exit); exitErr != nil {
		bestEffortRemoteRemove(cmd.Context(), toolClient, remoteArchivePath)
		message := strings.TrimSpace(stderr.String())
		if message == "" {
			return exitErr
		}
		return withRemoteStderr(exitErr, message)
	}
	return nil
}

func bestEffortRemoteRemove(ctx context.Context, toolClient *toolboxclient.Client, remotePath string) {
	cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), remoteArchiveCleanupTimeout)
	defer cancel()
	_, _ = toolClient.StreamProcess(cleanupCtx, toolboxclient.StreamProcessOptions{
		Request: tbgen.CreateProcessRequest{
			Argv:  &[]string{"rm", "-f", remotePath},
			Stdin: stdinModePtr(tbgen.Closed),
		},
		Stdout:      io.Discard,
		Stderr:      io.Discard,
		LogEncoding: "text",
	})
}

// destinationUploadPath names the remote file for an upload into a
// directory: the LOCAL side is an OS path (filepath — `C:\tmp\report.pdf`
// on Windows), the remote side is always POSIX.
func destinationUploadPath(localPath, remotePath string) string {
	if strings.HasSuffix(remotePath, "/") {
		return path.Join(remotePath, filepath.Base(localPath))
	}
	return remotePath
}

func recursiveDownloadPath(remotePath, localPath string) (string, error) {
	if recursiveDestinationInto(localPath) {
		return filepath.Join(filepath.Clean(localPath), path.Base(path.Clean(remotePath))), nil
	}
	info, err := os.Stat(localPath)
	if err == nil {
		if info.IsDir() {
			return filepath.Join(localPath, path.Base(path.Clean(remotePath))), nil
		}
		return localPath, nil
	}
	if !os.IsNotExist(err) {
		return "", err
	}
	return localPath, nil
}

func recursiveDestinationInto(localPath string) bool {
	return strings.HasSuffix(localPath, "/") || strings.HasSuffix(localPath, "\\")
}

// withRemoteStderr appends the remote tar's stderr to a process exit error.
// The printer renders a *cerr.Error's OWN message, so the text has to live
// in the cerr — a fmt.Errorf("%w: …") wrap around it would be dropped.
func withRemoteStderr(exitErr error, stderr string) error {
	message := strings.TrimSpace(stderr)
	if message == "" {
		return exitErr
	}
	e := cerr.New(cerr.ExitCode(exitErr), "%s: %s", exitErr.Error(), message)
	// The status is the remote process's: keep the source it was reported with.
	var ce *cerr.Error
	if errors.As(exitErr, &ce) {
		e.Source = ce.Source
	}
	return e
}
