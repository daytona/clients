// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// These codes are supplied by newer volume API contracts. Keep them next to
// volumes until the shared generated catalog is refreshed at integration.

// CodeVolumeNotFound identifies an absent volume (404).
const CodeVolumeNotFound = "VOLUME_NOT_FOUND"

// CodeVolumeFinalCommitPending identifies unsaved final volume writes (409).
const CodeVolumeFinalCommitPending = "VOLUME_FINAL_COMMIT_PENDING"

// CodeVolumesUnavailable identifies an unavailable volume service (503).
const CodeVolumesUnavailable = "VOLUMES_UNAVAILABLE"

// ErrVolumeNotFound matches VOLUME_NOT_FOUND independently of error source.
var ErrVolumeNotFound = codeSentinel{code: CodeVolumeNotFound, status: 404}

// ErrVolumeFinalCommitPending matches VOLUME_FINAL_COMMIT_PENDING independently
// of error source. Retry mounting once Volume.PendingFinalCommits is empty.
var ErrVolumeFinalCommitPending = codeSentinel{code: CodeVolumeFinalCommitPending, status: 409}

// ErrVolumesUnavailable matches VOLUMES_UNAVAILABLE independently of source.
var ErrVolumesUnavailable = codeSentinel{code: CodeVolumesUnavailable, status: 503}

// volumeCodeStatus supplements the base catalog without hand-editing generated
// files. It can disappear when the shared catalog learns these upstream codes.
func volumeCodeStatus(source, code string) int {
	if source == SourceAPI {
		switch code {
		case CodeVolumeNotFound:
			return 404
		case CodeVolumeFinalCommitPending:
			return 409
		case CodeVolumesUnavailable:
			return 503
		}
	}
	return catalogStatus(source, code)
}
