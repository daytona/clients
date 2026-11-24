// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"context"
	"errors"
	"io"
	"iter"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strings"
	"time"
)

func followBuildLogs(ctx context.Context, source buildLogSource,
	opts *FollowBuildLogsOptions,
) iter.Seq2[BuildLogEvent, error] {
	options := FollowBuildLogsOptions{}
	if opts != nil {
		options = *opts
	}
	return func(yield func(BuildLogEvent, error) bool) {
		path, err := buildLogDiscoveryPath(source.collection, source.id)
		if err == nil {
			err = validateFollowBuildLogs(options)
		}
		if err != nil {
			yield(nil, err)
			return
		}
		observationCtx, scope := newRequestScope(ctx, source.client, boundedTimeout(options.Timeout))
		defer scope.close()
		scope.kick()
		follower := buildLogFollower{
			api: source.client.api(), path: path,
			state: buildLogObservation{cursor: options.Cursor, offsetKnown: options.Cursor == ""},
			now:   time.Now, pause: buildLogPause, jitter: rand.Float64,
		}
		if followErr := follower.follow(observationCtx, yield); followErr != nil {
			yield(nil, followErr)
		}
	}
}

func validateFollowBuildLogs(options FollowBuildLogsOptions) error {
	if options.Cursor != "" && !buildLogCursorValid(options.Cursor) {
		return badRequest("Cursor must contain at most 2048 characters with no control characters")
	}
	if options.Timeout < 0 && options.Timeout != NoTimeout && options.Timeout != WaitForever {
		return badRequest("Timeout must be non-negative, NoTimeout or WaitForever")
	}
	return nil
}

type buildLogFollower struct {
	api      *apiClient
	path     string
	target   *buildLogTarget
	state    buildLogObservation
	failures int
	now      func() time.Time
	pause    func(context.Context, time.Duration) error
	jitter   func() float64
}

func (f *buildLogFollower) follow(ctx context.Context, yield func(BuildLogEvent, error) bool) error {
	for {
		if ctx.Err() != nil {
			return buildLogInterrupt(ctx, f.state.cursor, context.Cause(ctx))
		}
		var err error
		if f.target == nil {
			f.target, err = discoverBuildLogs(ctx, f.api, f.path)
			if err != nil && buildLogDiscoveryDenial(err) && ctx.Err() == nil {
				return err
			}
		}
		if err == nil {
			var finished bool
			finished, err = f.attempt(ctx, yield)
			if finished {
				return nil
			}
		}
		if ctx.Err() != nil || !buildLogRetryable(err) {
			return f.interrupted(ctx, err)
		}
		if f.failures >= 5 {
			cause := newSDKError(CodeBuildLogInterrupted, 0, "build log reconnect budget exhausted")
			cause.Err = err
			return buildLogInterrupt(ctx, f.state.cursor, cause)
		}
		delays := [...]time.Duration{
			250 * time.Millisecond, 500 * time.Millisecond, time.Second,
			2 * time.Second, 5 * time.Second,
		}
		// Backoff jitter is scheduling noise, not a security primitive.
		delay := time.Duration(float64(delays[f.failures]) * (0.8 + 0.2*f.jitter()))
		var sdkError *Error
		if errors.As(err, &sdkError) {
			delay = max(delay, sdkError.RetryAfter)
		}
		f.failures++
		if pauseErr := f.pause(ctx, delay); pauseErr != nil {
			return buildLogInterrupt(ctx, f.state.cursor, pauseErr)
		}
	}
}

func (f *buildLogFollower) interrupted(ctx context.Context, cause error) error {
	var interrupted *BuildLogInterruptedError
	if errors.As(cause, &interrupted) {
		interrupted.Cursor = f.state.cursor
	}
	return buildLogInterrupt(ctx, f.state.cursor, cause)
}

func (f *buildLogFollower) attempt(ctx context.Context,
	yield func(BuildLogEvent, error) bool,
) (finished bool, failure error) {
	query := url.Values{"follow": {"true"}, "format": {"sse"}}
	if f.state.cursor != "" {
		query.Set("cursor", f.state.cursor)
	}
	response, err := f.target.api.stream(ctx, apiRequest{
		Path: f.target.path, Query: query, Headers: http.Header{"Accept": {"text/event-stream"}},
		MaxResponseBytes: buildLogMaxEvent,
	})
	if err != nil {
		return false, err
	}
	defer closeResponse(ctx, response, &failure)
	media, _, _ := strings.Cut(response.Header.Get("Content-Type"), ";")
	if !strings.EqualFold(strings.TrimSpace(media), "text/event-stream") {
		return false, buildLogProtocol("expected text/event-stream")
	}
	started := f.now()
	defer func() {
		if f.now().Sub(started) >= 10*time.Second {
			f.failures = 0
		}
	}()
	decoder := buildLogDecoder{reader: bufio.NewReader(response.Body)}
	return f.consume(ctx, &decoder, yield)
}

func (f *buildLogFollower) consume(ctx context.Context, decoder *buildLogDecoder,
	yield func(BuildLogEvent, error) bool,
) (bool, error) {
	for {
		message, err := decoder.next()
		if errors.Is(err, io.EOF) {
			return false, buildLogEOF()
		}
		if err != nil {
			return false, transportError(ctx, err)
		}
		if ctx.Err() != nil {
			return false, context.Cause(ctx)
		}
		event, known, err := parseBuildLogMessage(message)
		if err != nil {
			return false, err
		}
		if !known {
			continue
		}
		event, next, err := f.state.prepare(event)
		if err != nil {
			return false, err
		}
		if !yield(event, nil) {
			return true, nil
		}
		if next.cursor != f.state.cursor {
			f.failures = 0
		}
		f.state = next
		if ctx.Err() != nil {
			return false, context.Cause(ctx)
		}
		done, err := buildLogEventOutcome(event)
		if done || err != nil {
			return done, err
		}
	}
}

// buildLogPermanentCodes are catalog codes a 429 / 503 response can carry
// that name a state a retry cannot change (a limit, or something the
// deployment does not configure). A response with one of them, or any 501
// (not implemented), is the caller's answer; every other 429 / 5xx,
// SERVICE_UNAVAILABLE included, is retried within the reconnect budget.
var buildLogPermanentCodes = map[string]bool{
	"LIMIT_EXCEEDED":                  true,
	"V0_BACKUP_REGISTRY_UNCONFIGURED": true,
}

func buildLogRetryable(err error) bool {
	var retry *buildLogRetry
	if errors.As(err, &retry) {
		return true
	}
	var sdkError *Error
	if errors.As(err, &sdkError) && (sdkError.StatusCode == 429 || sdkError.StatusCode >= 500) {
		return sdkError.StatusCode != http.StatusNotImplemented && !buildLogPermanentCodes[sdkError.Code]
	}
	return errors.Is(err, ErrConnection) || errors.Is(err, ErrClientTimeout)
}

func buildLogDiscoveryDenial(err error) bool {
	var sdkError *Error
	return errors.As(err, &sdkError) && sdkError.StatusCode != 0 && !buildLogRetryable(err)
}

func buildLogPause(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return context.Cause(ctx)
	case <-timer.C:
		return nil
	}
}
