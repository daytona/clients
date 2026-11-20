// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// LogChannel identifies process output. Future channels are kept in RawChannel.
type LogChannel string

const (
	// LogChannelUnknown represents an unrecognized channel.
	LogChannelUnknown LogChannel = "unknown"
	// LogChannelStdout contains standard output.
	LogChannelStdout LogChannel = "stdout"
	// LogChannelStderr contains standard error.
	LogChannelStderr LogChannel = "stderr"
	// LogChannelPTY contains terminal output.
	LogChannelPTY LogChannel = "pty"
	// LogChannelSetup contains provisioning narration, not command output.
	LogChannelSetup LogChannel = "setup"
	// LogChannelDisplay contains interpreter display data.
	LogChannelDisplay LogChannel = "display"
	// LogChannelExecuteResult contains an interpreter cell's main result.
	LogChannelExecuteResult LogChannel = "execute_result"
	// LogChannelError contains a structured interpreter error.
	LogChannelError LogChannel = "error"
)

// LogEncoding selects text or standard padded base64 log payloads.
type LogEncoding string

const (
	// LogEncodingUnknown represents an unknown encoding.
	LogEncodingUnknown LogEncoding = "unknown"
	// LogEncodingText decodes UTF-8 per channel across frames (default).
	LogEncodingText LogEncoding = "text"
	// LogEncodingBase64 preserves exact bytes as standard padded base64.
	LogEncodingBase64 LogEncoding = "base64"
)

// LogFrame is a replayable frame; Data uses the requested Encoding.
type LogFrame struct {
	// Cursor is the opaque resume position.
	Cursor string
	// Timestamp is when the frame was written.
	Timestamp time.Time
	// Channel is the normalized output channel.
	Channel LogChannel
	// RawChannel preserves the server's channel.
	RawChannel string
	// Data contains text or base64, according to Encoding.
	Data string
	// Encoding is the requested encoding (text by default).
	Encoding LogEncoding
	// RawEncoding preserves the selected encoding name.
	RawEncoding string
}

// ProcessLogsOptions selects a replay position and payload encoding.
type ProcessLogsOptions struct {
	// Cursor resumes after this position; empty begins at the retained head.
	Cursor string
	// Limit is the requested page size; zero uses the server default.
	Limit int
	// Encoding defaults to text.
	Encoding LogEncoding
}

// ProcessLogPage is one page of retained log frames.
type ProcessLogPage struct {
	// Items contains this page's frames.
	Items []LogFrame
	// NextCursor resumes after this page.
	NextCursor string
	// TruncatedHead reports frames evicted before this page.
	TruncatedHead bool
	// EOF means execution ended and no frames follow.
	EOF bool
	// FirstAvailableCursor identifies the oldest retained frame after eviction.
	FirstAvailableCursor string
}

// ProcessLogEvent is a closed union of frame, terminal-state, retention-warning,
// and EOF events. A stream error is yielded as err, not as an event.
type ProcessLogEvent interface{ processLogEvent() }

// ProcessLogFrameEvent delivers one log frame.
type ProcessLogFrameEvent struct {
	// Cursor is the event's resume position.
	Cursor string
	// Frame is the recorded output.
	Frame LogFrame
}

func (ProcessLogFrameEvent) processLogEvent() {}

// ProcessLogStateEvent reports a terminal process result.
type ProcessLogStateEvent struct {
	// Cursor is the event's resume position.
	Cursor string
	// Result reports only the outcome fields the server supplied.
	Result WaitResult
}

func (ProcessLogStateEvent) processLogEvent() {}

// ProcessLogWarningEvent reports retention loss.
type ProcessLogWarningEvent struct {
	// Cursor is the event's resume position.
	Cursor string
	// Message describes the warning.
	Message string
	// FirstAvailableCursor is the earliest retained frame.
	FirstAvailableCursor string
}

func (ProcessLogWarningEvent) processLogEvent() {}

// ProcessLogEOFEvent marks a complete log stream.
type ProcessLogEOFEvent struct {
	// Cursor is the final resume position.
	Cursor string
}

func (ProcessLogEOFEvent) processLogEvent() {}

func processLogChannel(raw string) LogChannel {
	switch raw {
	case "stdout":
		return LogChannelStdout
	case "stderr":
		return LogChannelStderr
	case "pty":
		return LogChannelPTY
	case "setup":
		return LogChannelSetup
	case "display":
		return LogChannelDisplay
	case "execute_result":
		return LogChannelExecuteResult
	case "error":
		return LogChannelError
	default:
		return LogChannelUnknown
	}
}
