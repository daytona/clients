// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/base64"
	"strconv"
)

// buildLogFields confines wire scratch values and the first boundary failure.
type buildLogFields struct {
	object jsonObject
	err    error
}

func (f *buildLogFields) text(name string, required bool) string {
	value, err := field[string](f.object, name, required)
	if f.err == nil {
		f.err = err
	}
	return value
}

func (f *buildLogFields) boolean(name string) bool {
	value, err := field[bool](f.object, name, true)
	if f.err == nil {
		f.err = err
	}
	return value
}

func (f *buildLogFields) offset(name string) uint64 {
	text := f.text(name, true)
	value, err := strconv.ParseUint(text, 10, 64)
	if f.err == nil && (err != nil || strconv.FormatUint(value, 10) != text) {
		f.err = buildLogProtocol("invalid uint64 offset: " + name)
	}
	return value
}

func parseBuildLogMessage(message buildLogMessage) (BuildLogEvent, bool, error) {
	switch message.name {
	case "state", "log", "gap", "unavailable", "source_changed", "done", "error":
	default:
		return nil, false, nil // Future events are intentionally skipped unparsed.
	}
	if err := checkJSON([]byte(message.data)); err != nil {
		return nil, false, buildLogProtocol(err.Error())
	}
	object, err := parseObject([]byte(message.data), "build log event")
	if err != nil {
		return nil, false, buildLogProtocol(err.Error())
	}
	switch message.name {
	case "log", "gap", "done":
	default:
		if message.id != nil {
			return nil, false, buildLogProtocol("control event must not advance cursor")
		}
	}
	fields := buildLogFields{object: object}
	event := fields.event(message)
	if fields.err != nil {
		return nil, false, buildLogProtocol(fields.err.Error())
	}
	return event, true, nil
}

func (f *buildLogFields) event(message buildLogMessage) BuildLogEvent {
	switch message.name {
	case "state":
		event := BuildLogStateEvent{Status: f.text("status", true), Error: f.text("error", false)}
		if _, err := field[string](f.object, "deadline_at", false); err != nil {
			if f.err == nil {
				f.err = buildLogProtocol("deadline_at must be a string")
			}
			return event
		}
		stamp, err := timestampField(f.object, "deadline_at")
		if f.err == nil {
			f.err = err
		}
		event.DeadlineAt = stamp
		return event
	case "log":
		return f.chunk(message.id)
	case "gap":
		event := BuildLogGapEvent{
			RawReason: f.text("reason", true), RequestedOffset: f.offset("requested_offset"),
			FirstAvailableOffset: f.offset("first_available_offset"), DroppedBytes: f.offset("dropped_bytes"),
			Cursor: f.checkpoint(message.id), Reason: BuildLogGapReasonUnknown,
		}
		if event.RawReason == string(BuildLogGapReasonTruncated) {
			event.Reason = BuildLogGapReasonTruncated
		}
		return event
	case "unavailable":
		return BuildLogUnavailableEvent{Reason: f.text("reason", true)}
	case "source_changed":
		return BuildLogSourceChangedEvent{Reason: f.text("reason", true)}
	case "error":
		return BuildLogErrorEvent{
			Error: f.text("error", true), Code: f.text("code", true),
			Retryable: f.boolean("retryable"), TraceID: f.text("trace_id", false),
		}
	case "done":
		return f.done(message.id)
	default:
		f.err = buildLogProtocol("unknown build log event")
		return nil
	}
}

func (f *buildLogFields) checkpoint(id *string) string {
	if id == nil || !buildLogCursorValid(*id) {
		f.err = buildLogProtocol("invalid or missing build log cursor")
		return ""
	}
	return *id
}

func (f *buildLogFields) chunk(id *string) BuildLogChunkEvent {
	encoded := f.text("data_base64", true)
	event := BuildLogChunkEvent{
		StartOffset: f.offset("start_offset"), NextOffset: f.offset("next_offset"), Cursor: f.checkpoint(id),
	}
	if len(encoded) > 87384 {
		f.err = buildLogProtocol("oversized log chunk")
		return event
	}
	data, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(data) == 0 || len(data) > 65536 || base64.StdEncoding.EncodeToString(data) != encoded {
		f.err = buildLogProtocol("invalid log chunk base64")
	}
	event.Data = data
	return event
}

func (f *buildLogFields) done(id *string) BuildLogDoneEvent {
	result := BuildLogFollowResult{
		Status: f.text("status", true), RawReason: f.text("reason", true), OutputComplete: f.boolean("output_complete"),
	}
	result.Reason = buildLogDoneReason(result.RawReason)
	if present(f.object["cursor"]) {
		text := f.text("cursor", true)
		result.Cursor = f.checkpoint(&text)
	}
	if id != nil {
		checkpoint := f.checkpoint(id)
		if result.Cursor != "" && checkpoint != result.Cursor {
			f.err = buildLogProtocol("done cursor differs from event ID")
		}
		result.Cursor = checkpoint
	}
	return BuildLogDoneEvent{Result: result}
}

func buildLogDoneReason(raw string) BuildLogDoneReason {
	switch BuildLogDoneReason(raw) {
	case BuildLogDoneReasonTerminal:
		return BuildLogDoneReasonTerminal
	case BuildLogDoneReasonDeadline:
		return BuildLogDoneReasonDeadline
	case BuildLogDoneReasonNotApplicable:
		return BuildLogDoneReasonNotApplicable
	case BuildLogDoneReasonResourceDeleted:
		return BuildLogDoneReasonResourceDeleted
	case BuildLogDoneReasonUnknown:
		return BuildLogDoneReasonUnknown
	default:
		return BuildLogDoneReasonUnknown
	}
}
