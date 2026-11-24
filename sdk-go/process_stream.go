// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"iter"
	"net/http"
	"sync"
	"time"
)

// errProcessLogIdle ends a follow stream that delivered no bytes for its idle
// bound: on a path that drops quiet connections without closing them, a read
// would otherwise block until the caller's deadline.
var errProcessLogIdle = errors.New("process log stream idle")

// StreamLogs follows the log through an explicit EOF event. Missing EOF is
// ErrConnection, never a silently complete log. Every event is bounded by the
// client's max response size. Resume with the last event's cursor after failure.
// Breaking iteration closes the response; ctx and Client.Close cancel blocked reads.
func (h *ProcessHandle) StreamLogs(ctx context.Context, opts *ProcessLogsOptions) iter.Seq2[ProcessLogEvent, error] {
	return h.streamLogs(ctx, opts, 0)
}

// streamLogs is StreamLogs with an idle bound: a stream that delivers no bytes
// for idle (zero: no bound) ends with errProcessLogIdle.
func (h *ProcessHandle) streamLogs(
	ctx context.Context, opts *ProcessLogsOptions, idle time.Duration,
) iter.Seq2[ProcessLogEvent, error] {
	var copied ProcessLogsOptions
	if opts != nil {
		copied = *opts
	}
	return func(yield func(ProcessLogEvent, error) bool) {
		query, err := processLogQuery(&copied)
		if err != nil {
			yield(nil, err)
			return
		}
		query.Set("follow", "true")
		segment, err := pathSegment(h.id)
		if err != nil {
			yield(nil, err)
			return
		}
		headers := http.Header{"Accept": {"text/event-stream"}}
		if copied.Cursor != "" {
			headers.Set("Last-Event-ID", copied.Cursor)
		}
		stream := processLogStream{
			toolbox:  h.sandbox.toolbox(),
			request:  toolboxRequest{Path: "/processes/" + segment + "/logs", Query: query, Headers: headers},
			encoding: processLogEncoding(&copied),
			limit:    h.sandbox.client.config.maxResponseBytes,
			idle:     idle,
		}
		stopped, err := stream.consume(ctx, yield)
		if !stopped && err != nil {
			yield(nil, err)
		}
	}
}

type processLogStream struct {
	toolbox  *toolboxClient
	request  toolboxRequest
	encoding LogEncoding
	limit    int64
	idle     time.Duration
}

// idleBody records when bytes last arrived for the stream's idle watchdog.
type idleBody struct {
	io.Reader
	watch *idleWatch
}

func (b idleBody) Read(p []byte) (int, error) {
	n, err := b.Reader.Read(p)
	if n > 0 {
		b.watch.kick()
	}
	return n, err
}

// idleWatch cancels a stream once no bytes arrived for idle. Its timer checks
// the last arrival under the lock and re-arms for the rest of the window, so
// a read that arrived in time never loses to a timer that already fired.
type idleWatch struct {
	mu      sync.Mutex
	last    time.Time
	timer   *time.Timer
	stopped bool
}

func newIdleWatch(idle time.Duration, expire func()) *idleWatch {
	watch := &idleWatch{last: time.Now()}
	watch.mu.Lock()
	defer watch.mu.Unlock()
	watch.timer = time.AfterFunc(idle, func() {
		watch.mu.Lock()
		defer watch.mu.Unlock()
		if watch.stopped {
			return
		}
		if quiet := time.Since(watch.last); quiet < idle {
			watch.timer.Reset(idle - quiet)
			return
		}
		expire()
	})
	return watch
}

func (w *idleWatch) kick() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.last = time.Now()
}

func (w *idleWatch) stop() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.stopped = true
	w.timer.Stop()
}

// open sends the follow request. With an idle bound, the idle window starts
// before the request, so a follow request whose response headers never arrive
// is bounded by it too, and the returned body kicks it as bytes arrive.
func (s processLogStream) open(ctx context.Context, cancel context.CancelCauseFunc) (
	*http.Response, io.Reader, func(), error,
) {
	stop := func() {}
	var watch *idleWatch
	if s.idle > 0 {
		watch = newIdleWatch(s.idle, func() { cancel(errProcessLogIdle) })
		stop = watch.stop
	}
	response, err := s.toolbox.stream(ctx, s.request)
	if err != nil {
		return nil, nil, stop, err
	}
	if watch == nil {
		return response, response.Body, stop, nil
	}
	watch.kick()
	return response, idleBody{Reader: response.Body, watch: watch}, stop, nil
}

func (s processLogStream) consume(ctx context.Context, yield func(ProcessLogEvent, error) bool) (
	stopped bool, failure error,
) {
	streamCtx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	idled := func() bool { return ctx.Err() == nil && errors.Is(context.Cause(streamCtx), errProcessLogIdle) }
	response, body, stop, openErr := s.open(streamCtx, cancel)
	defer stop()
	if openErr != nil {
		if idled() {
			return false, errProcessLogIdle
		}
		return false, openErr
	}
	defer closeResponse(ctx, response, &failure)
	reader := newProcessSSEReader(body, s.limit)
	for {
		message, err := reader.next()
		if err != nil {
			if idled() {
				return false, errProcessLogIdle
			}
			if errors.Is(err, io.EOF) {
				return false, wrapError(transportError(ctx, io.ErrUnexpectedEOF), "process log stream ended before eof")
			}
			return false, transportError(ctx, err)
		}
		event, err := parseProcessLogEvent(message, s.encoding)
		if err != nil {
			return false, err
		}
		if event == nil {
			continue
		}
		if !yield(event, nil) {
			return true, nil
		}
		switch event.(type) {
		case ProcessLogEOFEvent:
			return false, nil
		case ProcessLogFrameEvent, ProcessLogStateEvent, ProcessLogWarningEvent:
			// More frames can follow these events.
		}
	}
}

func parseProcessLogEvent(message processSSEMessage, encoding LogEncoding) (ProcessLogEvent, error) {
	switch message.event {
	case "log", "state", "warning", "eof", "error":
	default:
		return nil, nil //nolint:nilnil // Unknown SSE event names are ignored without parsing their data.
	}
	data := []byte(message.data)
	if len(data) == 0 {
		data = []byte("{}")
	}
	if err := checkJSON(data); err != nil {
		return nil, err
	}
	object, err := parseObject(data, "process log event")
	if err != nil {
		return nil, err
	}
	if message.event == "error" {
		return nil, processStreamError(object)
	}
	cursor := message.id
	if _, exists := object["cursor"]; exists {
		cursor, err = field[string](object, "cursor", true)
		if err != nil {
			return nil, err
		}
	}
	if cursor == "" {
		return nil, badRequest("log event cursor is required")
	}
	switch message.event {
	case "log":
		frame, parseErr := processEventFrame(object, encoding, cursor)
		if parseErr != nil {
			return nil, parseErr
		}
		return ProcessLogFrameEvent{Cursor: cursor, Frame: frame}, nil
	case "state":
		info, parseErr := parseProcess(data)
		if parseErr != nil {
			return nil, parseErr
		}
		return ProcessLogStateEvent{Cursor: cursor, Result: info.WaitResult}, nil
	case "warning":
		text, textErr := field[string](object, "message", true)
		first, firstErr := field[string](object, "firstAvailableCursor", true)
		if err = errors.Join(textErr, firstErr); err != nil {
			return nil, err
		}
		return ProcessLogWarningEvent{Cursor: cursor, Message: text, FirstAvailableCursor: first}, nil
	default:
		return ProcessLogEOFEvent{Cursor: cursor}, nil
	}
}

func processEventFrame(object jsonObject, encoding LogEncoding, cursor string) (LogFrame, error) {
	encoded, err := json.Marshal(cursor)
	if err != nil {
		return LogFrame{}, wrapJSONError(err)
	}
	object["cursor"] = encoded
	raw, err := json.Marshal(object)
	if err != nil {
		return LogFrame{}, wrapJSONError(err)
	}
	return parseProcessFrame(raw, encoding)
}

func processStreamError(object jsonObject) error {
	message, err := field[string](object, "message", true)
	if err != nil {
		return err
	}
	source := optionalErrorString(object, "source")
	if source == "" {
		source = "DAYTONA_DAEMON"
	}
	code := optionalErrorString(object, "code")
	return &Error{Message: message, Code: code, Source: source}
}
