// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"sync"
	"time"
)

// TransferProgress reports cumulative file bytes, excluding multipart framing.
type TransferProgress struct {
	// Bytes is the number of file bytes transferred.
	Bytes int64
	// Total is the expected length; nil means unknown.
	Total *int64
}

// UploadOptions configures uploads from bytes, local paths, or readers.
type UploadOptions struct {
	// Atomic writes a temporary file then renames it. Unsupported servers return
	// ErrAtomicUploadUnsupported; the SDK never silently retries non-atomically.
	Atomic bool
	// OnProgress runs synchronously with monotonically increasing byte counts.
	// Keep it short. Panics are contained, like callback failures in the other SDKs.
	OnProgress func(TransferProgress)
	// Timeout replaces the client's transfer idle timeout. Zero uses the default;
	// NoTimeout disables it. Transfers have no total SDK deadline.
	Timeout time.Duration
}

// DownloadOptions configures a streaming download, which has no total size cap.
type DownloadOptions struct {
	// OnProgress runs synchronously after writes; Total is nil when not reported.
	// Keep it short. Callback panics are contained.
	OnProgress func(TransferProgress)
	// Timeout replaces the client's idle timeout; zero uses it, NoTimeout disables it.
	Timeout time.Duration
}

// ReadFileOptions configures an in-memory read, including the whole ReadMany body.
type ReadFileOptions struct {
	// MaxResponseBytes replaces the client cap (default 1 GiB); zero uses it,
	// NoLimit disables it. Larger responses return ErrPayloadTooLarge.
	MaxResponseBytes int64
	// Timeout replaces the client's idle timeout; zero uses it, NoTimeout disables it.
	Timeout time.Duration
}

// WriteFileOptions configures WriteFile. It shares the upload transfer contract.
type WriteFileOptions = UploadOptions

// WriteManyOptions configures WriteMany; atomicity is per file, not per batch.
type WriteManyOptions struct {
	// Atomic requests temporary-file-plus-rename for every file.
	Atomic bool
	// Timeout replaces the client idle timeout; NoTimeout disables it.
	Timeout time.Duration
}

func fileTimeout(timeout time.Duration) error {
	if timeout < 0 && timeout != NoTimeout {
		return badRequest("Timeout must be nonnegative or NoTimeout")
	}
	return nil
}

func fileTransferError(ctx context.Context, err error) error {
	if errors.Is(ctx.Err(), context.Canceled) {
		result := newSDKError(CodeRequestCancelled, 499, "file transfer cancelled")
		result.Err = context.Canceled
		if cause := context.Cause(ctx); cause != nil && !errors.Is(result.Err, cause) {
			result.Err = errors.Join(result.Err, cause)
		}
		return result
	}
	return transportError(ctx, err)
}

type fileProgress struct {
	mu       sync.Mutex
	callback func(TransferProgress)
	last     int64
}

func (p *fileProgress) report(bytes int64, total *int64) {
	// This private observer gate cannot be reentered by user code. Serialize the
	// callback with the high-water mark so overlapping retry teardown cannot
	// deliver an older count after a newer one.
	p.mu.Lock()
	defer p.mu.Unlock()
	if bytes <= p.last {
		return
	}
	p.last = bytes
	if p.callback != nil {
		// User callbacks are observational and must not turn a transfer into a failure.
		defer func() {
			if recover() != nil {
				// Progress is observational; a user panic does not abort file I/O.
				return
			}
		}()
		progress := TransferProgress{Bytes: bytes}
		if total != nil {
			value := *total
			progress.Total = &value
		}
		p.callback(progress)
	}
}
