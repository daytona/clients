// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

type buildLogObservation struct {
	cursor      string
	offset      uint64
	offsetKnown bool
	gap         bool
}

func (o *buildLogObservation) prepare(event BuildLogEvent) (BuildLogEvent, buildLogObservation, error) {
	next := *o
	switch value := event.(type) {
	case BuildLogChunkEvent:
		if !o.advances(value.StartOffset, value.NextOffset, uint64(len(value.Data))) {
			return nil, next, buildLogProtocol("noncontiguous log offsets")
		}
		if value.Cursor == o.cursor {
			return nil, next, buildLogProtocol("cursor did not advance with bytes")
		}
		next.cursor, next.offset, next.offsetKnown = value.Cursor, value.NextOffset, true
	case BuildLogGapEvent:
		if !o.advances(value.RequestedOffset, value.FirstAvailableOffset, value.DroppedBytes) {
			return nil, next, buildLogProtocol("invalid log gap")
		}
		if value.Cursor == o.cursor {
			return nil, next, buildLogProtocol("cursor did not advance with bytes")
		}
		next.cursor, next.offset, next.offsetKnown, next.gap = value.Cursor, value.FirstAvailableOffset, true, true
	case BuildLogDoneEvent:
		if value.Result.Cursor == "" {
			value.Result.Cursor = o.cursor
		} else if o.cursor != "" && o.cursor != value.Result.Cursor {
			return nil, next, buildLogProtocol("done advanced cursor without bytes")
		}
		value.Result.OutputComplete = value.Result.OutputComplete && !o.gap
		next.cursor = value.Result.Cursor
		event = value
	case BuildLogStateEvent, BuildLogUnavailableEvent, BuildLogSourceChangedEvent, BuildLogErrorEvent:
		// These control events do not alter the checkpoint.
	default:
		return nil, next, buildLogProtocol("unknown build log event")
	}
	return event, next, nil
}

func (o buildLogObservation) advances(start, end, count uint64) bool {
	return end > start && end-start == count && (!o.offsetKnown || start == o.offset)
}

func buildLogEventOutcome(event BuildLogEvent) (bool, error) {
	switch value := event.(type) {
	case BuildLogSourceChangedEvent:
		return false, newSDKError("BUILD_LOG_SOURCE_CHANGED", 0, value.Reason)
	case BuildLogErrorEvent:
		cause := newSDKError(value.Code, 0, value.Error)
		cause.TraceID = value.TraceID
		if value.Retryable {
			return false, &buildLogRetry{cause: cause}
		}
		return false, &BuildLogInterruptedError{SDKError: cause}
	case BuildLogDoneEvent:
		return true, nil
	case BuildLogStateEvent, BuildLogUnavailableEvent, BuildLogChunkEvent, BuildLogGapEvent:
		return false, nil
	default:
		return false, buildLogProtocol("unknown build log event")
	}
}
