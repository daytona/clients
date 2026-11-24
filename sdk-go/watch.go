// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"context"
	"errors"
	"io"
	"iter"
	"net/http"
	"strconv"
	"sync"
)

// WatchOptions configures directory observation.
type WatchOptions struct {
	// Recursive includes descendants; default false.
	Recursive bool
	// OnExit runs once after delivery ends. Failures before Watch returns never
	// call it. Panics are contained. It may call Stop or Err, but must not Wait.
	// Delivery starts when Events, Wait, or Stop is called, so no callback can
	// run before the caller receives the handle.
	OnExit func(error)
}

// WatchHandle owns one directory stream. Call Stop when done, even if Events is
// never consumed. The context passed to Watch or Client.Close also closes the
// HTTP stream. Events is single-consumer; all other methods are concurrency-safe.
// A server error event (for example, a macOS access denial) ends the watch with
// its typed error, reported by Err, Wait, Events and OnExit.
type WatchHandle struct {
	ctx     context.Context
	cancel  context.CancelFunc
	stream  io.ReadCloser
	parser  *fileSSE
	held    []FilesystemEvent
	onExit  func(error)
	start   sync.Once
	stop    sync.Once
	mu      sync.Mutex
	stopped bool
	exitErr error
	events  chan FilesystemEvent
	done    chan struct{}
}

// Watch opens a directory stream and waits for its start handshake. Startup is
// limited to 4096 events and 1 MiB; subsequent frames use the client response cap.
// No request timeout applies after HTTP headers. Unknown SSE names are ignored.
// A server error event before start fails this call without invoking OnExit.
func (f *FileSystem) Watch(ctx context.Context, path string, opts *WatchOptions) (result *WatchHandle, failure error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	options := WatchOptions{}
	if opts != nil {
		options = *opts
		query.Set("recursive", strconv.FormatBool(options.Recursive))
	}
	watchCtx, cancel := context.WithCancel(ctx)
	response, err := f.sandbox.toolbox().stream(watchCtx, toolboxRequest{
		Path: "/files/watch", Query: query, Headers: http.Header{"Accept": {"text/event-stream"}},
	})
	if err != nil {
		cancel()
		return nil, err
	}
	defer func() {
		if result == nil {
			cancel()
			closeResponse(ctx, response, &failure)
		}
	}()
	watch := &WatchHandle{
		ctx: watchCtx, cancel: cancel, stream: response.Body, onExit: options.OnExit,
		parser: &fileSSE{reader: bufio.NewReader(response.Body), limit: f.sandbox.client.config.maxResponseBytes},
		events: make(chan FilesystemEvent), done: make(chan struct{}),
	}
	if handshakeErr := watch.handshake(); handshakeErr != nil {
		return nil, handshakeErr
	}
	if contextErr := ctx.Err(); contextErr != nil {
		return nil, transportError(ctx, contextErr)
	}
	return watch, nil
}

func (w *WatchHandle) handshake() error {
	for {
		message, err := w.parser.next()
		if err != nil {
			return fileWatchReadError(transportContextError(w.ctx), err)
		}
		if message.event == "start" {
			w.parser.started = true
			return nil
		}
		event, err := parseWatchEvent(message)
		if err != nil {
			return err
		}
		if event != nil {
			if len(w.held) == fileWatchStartupEvents {
				return badRequest("filesystem watch exceeded 4096 events before start")
			}
			w.held = append(w.held, *event)
		}
	}
}

func transportContextError(ctx context.Context) error {
	if ctx.Err() != nil {
		return transportError(ctx, ctx.Err())
	}
	return nil
}

func (w *WatchHandle) activate() { w.start.Do(func() { go w.run() }) }

// Stop requests shutdown without waiting, so it is safe inside OnExit.
// Use Wait to join shutdown and the exit callback. Stop is idempotent.
func (w *WatchHandle) Stop() {
	w.stop.Do(func() {
		w.mu.Lock()
		w.stopped = true
		w.mu.Unlock()
		w.cancel()
	})
	w.activate()
}

// Wait waits for shutdown and OnExit and returns the watch's terminal error.
// It does not consume events: use Events concurrently if the watch is active.
// Canceling this wait does not stop the watch.
func (w *WatchHandle) Wait(ctx context.Context) error {
	w.activate()
	select {
	case <-w.done:
		return w.Err()
	case <-ctx.Done():
		return transportError(ctx, ctx.Err())
	}
}

// Err is nil while running or after Stop; otherwise it is the terminal SDK error.
func (w *WatchHandle) Err() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.exitErr
}

// Events yields file and overflow events in order. Breaking iteration or
// canceling ctx stops the watch; Wait joins cleanup. A terminal error is yielded
// once. Backpressure is bounded, with no unbounded client-side event queue.
func (w *WatchHandle) Events(ctx context.Context) iter.Seq2[FilesystemEvent, error] {
	return func(yield func(FilesystemEvent, error) bool) {
		w.activate()
		defer w.Stop()
		for {
			select {
			case event, open := <-w.events:
				if !open {
					if err := w.Err(); err != nil {
						yield(FilesystemEvent{}, err)
					}
					return
				}
				if !yield(event, nil) {
					return
				}
			case <-ctx.Done():
				yield(FilesystemEvent{}, transportError(ctx, ctx.Err()))
				return
			}
		}
	}
}

func (w *WatchHandle) run() {
	err := w.deliver()
	w.mu.Lock()
	if w.stopped && (errors.Is(err, context.Canceled) || errors.Is(err, io.EOF)) {
		err = nil
	}
	w.mu.Unlock()
	closeErr := w.stream.Close()
	w.cancel()
	w.mu.Lock()
	if !w.stopped && closeErr != nil {
		err = errors.Join(err, transportError(w.ctx, closeErr))
	}
	w.exitErr = err
	w.mu.Unlock()
	close(w.events)
	if w.onExit != nil {
		func() {
			defer func() {
				if recover() != nil {
					// Exit observers cannot alter the already recorded terminal result.
					return
				}
			}()
			w.onExit(err)
		}()
	}
	close(w.done)
}

func (w *WatchHandle) deliver() error {
	for _, event := range w.held {
		if err := w.send(event); err != nil {
			return err
		}
	}
	w.held = nil
	for {
		message, err := w.parser.next()
		if err != nil {
			return fileWatchReadError(transportContextError(w.ctx), err)
		}
		event, err := parseWatchEvent(message)
		if err != nil {
			return err
		}
		if event != nil {
			if sendErr := w.send(*event); sendErr != nil {
				return sendErr
			}
		}
	}
}

func (w *WatchHandle) send(event FilesystemEvent) error {
	if err := w.ctx.Err(); err != nil {
		return transportError(w.ctx, err)
	}
	select {
	case <-w.ctx.Done():
		return transportError(w.ctx, w.ctx.Err())
	case w.events <- event:
		return nil
	}
}
