// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// FollowBuildLogsOptions configures resumable build observation.
type FollowBuildLogsOptions struct {
	// Cursor resumes after a delivered checkpoint; empty starts at byte zero.
	Cursor string
	// Timeout bounds discovery, reconnects and observation. Zero, NoTimeout,
	// WaitForever or a value above 2,147,483.647 seconds adds no deadline.
	// The context still bounds every operation.
	Timeout time.Duration
}

// BuildLogsSnapshot contains retained output and server state as plain text.
// Output may be incomplete while building; truncation is indicated in Text.
type BuildLogsSnapshot struct {
	// Text is the server's retained build-log snapshot.
	Text string
}

// BuildLogDoneReason explains why observation completed.
type BuildLogDoneReason string

const (
	// BuildLogDoneReasonUnknown preserves a future reason in RawReason.
	BuildLogDoneReasonUnknown BuildLogDoneReason = "unknown"
	// BuildLogDoneReasonTerminal means the build reached a terminal state.
	BuildLogDoneReasonTerminal BuildLogDoneReason = "terminal"
	// BuildLogDoneReasonDeadline means the server observation deadline passed.
	BuildLogDoneReasonDeadline BuildLogDoneReason = "deadline"
	// BuildLogDoneReasonNotApplicable means this resource has no build output.
	BuildLogDoneReasonNotApplicable BuildLogDoneReason = "not_applicable"
	// BuildLogDoneReasonResourceDeleted means the resource was deleted.
	BuildLogDoneReasonResourceDeleted BuildLogDoneReason = "resource_deleted"
)

// BuildLogGapReason explains missing output.
type BuildLogGapReason string

const (
	// BuildLogGapReasonUnknown preserves a future reason in RawReason.
	BuildLogGapReasonUnknown BuildLogGapReason = "unknown"
	// BuildLogGapReasonTruncated means earlier output was discarded.
	BuildLogGapReasonTruncated BuildLogGapReason = "truncated"
)

// BuildLogFollowResult describes explicit completion, including failed builds.
// Status is an open server vocabulary; completion does not imply build success.
type BuildLogFollowResult struct {
	// Status is the server's build status.
	Status string
	// Reason is the normalized completion reason.
	Reason BuildLogDoneReason
	// RawReason is the server's reason verbatim.
	RawReason string
	// Cursor is the final checkpoint, or empty when none was supplied.
	Cursor string
	// OutputComplete is false if any gap was observed.
	OutputComplete bool
}

// BuildLogEvent is one of BuildLogStateEvent, BuildLogChunkEvent,
// BuildLogGapEvent, BuildLogUnavailableEvent, BuildLogSourceChangedEvent,
// BuildLogDoneEvent or BuildLogErrorEvent. Use a type switch to inspect it.
type BuildLogEvent interface{ buildLogEvent() }

// BuildLogStateEvent reports build state without advancing the checkpoint.
type BuildLogStateEvent struct {
	// Status is the open server build status.
	Status string
	// Error is the optional server failure detail.
	Error string
	// DeadlineAt is server execution metadata, not an observation timeout.
	// Zero means absent.
	DeadlineAt time.Time
}

func (BuildLogStateEvent) buildLogEvent() {}

// BuildLogChunkEvent contains caller-owned bytes and their checkpoint.
// Persist Data and Cursor together before requesting the next event.
type BuildLogChunkEvent struct {
	// Data is the decoded log chunk, at most 64 KiB; it need not be UTF-8.
	Data []byte
	// StartOffset is the inclusive byte offset.
	StartOffset uint64
	// NextOffset is the exclusive byte offset.
	NextOffset uint64
	// Cursor resumes immediately after these bytes.
	Cursor string
}

func (BuildLogChunkEvent) buildLogEvent() {}

// BuildLogGapEvent reports discarded bytes and advances the checkpoint.
type BuildLogGapEvent struct {
	// Reason is the normalized gap reason.
	Reason BuildLogGapReason
	// RawReason is the server's reason verbatim.
	RawReason string
	// RequestedOffset is the byte offset requested.
	RequestedOffset uint64
	// FirstAvailableOffset is the earliest retained byte offset.
	FirstAvailableOffset uint64
	// DroppedBytes is the number of missing bytes.
	DroppedBytes uint64
	// Cursor resumes at FirstAvailableOffset.
	Cursor string
}

func (BuildLogGapEvent) buildLogEvent() {}

// BuildLogUnavailableEvent reports temporarily unavailable output.
type BuildLogUnavailableEvent struct {
	// Reason is the server explanation.
	Reason string
}

func (BuildLogUnavailableEvent) buildLogEvent() {}

// BuildLogSourceChangedEvent reports replacement of the observed log source.
// The next iterator value is a BuildLogInterruptedError; no restart occurs.
type BuildLogSourceChangedEvent struct {
	// Reason is the server explanation.
	Reason string
}

func (BuildLogSourceChangedEvent) buildLogEvent() {}

// BuildLogDoneEvent is the last event of a successfully completed observation.
type BuildLogDoneEvent struct {
	// Result describes completion and output completeness.
	Result BuildLogFollowResult
}

func (BuildLogDoneEvent) buildLogEvent() {}

// BuildLogErrorEvent reports a server stream error before retry or interruption.
type BuildLogErrorEvent struct {
	// Error is the server explanation.
	Error string
	// Code is the open server error code.
	Code string
	// Retryable determines whether observation reconnects.
	Retryable bool
	// TraceID is the optional server trace identifier.
	TraceID string
}

func (BuildLogErrorEvent) buildLogEvent() {}
