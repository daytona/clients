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
	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

const presignedTransferTTL = time.Hour
const remoteArchiveCleanupTimeout = 10 * time.Second

func newSandboxCPSubcommand() *cobra.Command {
	var recursive bool
	cmd := &cobra.Command{
		Use:   "cp <src> <dst>",
		Short: "Copy files to or from a sandbox",
		Long:  "Copies one file over a pre-signed toolbox URL minted by the API (POST /sandboxes/{id}/files/sign). A file copied onto an existing sandbox directory lands inside it, as with POSIX cp; a local directory needs -r. With -r, destination handling matches POSIX cp -r: if the destination ends with / (or is an existing local directory on download), the source directory is copied into it as <dest>/<basename(src)>; otherwise the destination path is the new directory itself.",
		Args:  cobra.ExactArgs(2),
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
			if dstRemote {
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
					return recursiveDownload(cmd, toolClient, src.Path, args[1])
				}
				signedURL, err := toolboxclient.SignFileURL(cmd.Context(), signer, string(target.Detail.Class), target.Detail.Id, http.MethodGet, src.Path, presignedTransferTTL)
				if err != nil {
					return err
				}
				return toolClient.DownloadFile(cmd.Context(), signedURL, toolboxclient.SingleFileLocalDestination(args[1], src.Path))
			}
			toolClient, target, _, signer, err := sandboxClients(cmd.Context(), a, dst.SandboxID)
			if err != nil {
				return err
			}
			if recursive {
				return recursiveUpload(cmd, toolClient, signer, target, args[0], dst.Path)
			}
			remotePath := destinationUploadPath(args[0], dst.Path)
			if remotePath == dst.Path {
				isDir, err := remoteIsDir(cmd.Context(), toolClient, dst.Path)
				if err != nil {
					return err
				}
				if isDir {
					remotePath = path.Join(dst.Path, filepath.Base(args[0]))
				}
			}
			signedURL, err := toolboxclient.SignFileURL(cmd.Context(), signer, string(target.Detail.Class), target.Detail.Id, http.MethodPost, remotePath, presignedTransferTTL)
			if err != nil {
				return err
			}
			return toolClient.UploadFile(cmd.Context(), signedURL, args[0], remotePath)
		},
	}
	cmd.Flags().BoolVarP(&recursive, "recursive", "r", false, "copy a directory by uploading one tar archive, then extracting it remotely")
	return cmd
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
