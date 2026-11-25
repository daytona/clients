// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

// Every ListAll reads a failed page again after a transient failure, at most
// three reads, and the error that ends the walk carries the cursor of the page
// that failed, so the walk resumes there with no item repeated or skipped.
// Python's twin is tests/test_iter_pages.py, TypeScript's tests/iter-pages.test.ts.

func recordPageRetryPauses(t *testing.T) *[]time.Duration {
	t.Helper()
	var mu sync.Mutex
	pauses := []time.Duration{}
	previous := pageRetryPause
	pageRetryPause = func(ctx context.Context, delay time.Duration) error {
		mu.Lock()
		defer mu.Unlock()
		pauses = append(pauses, delay)
		return ctx.Err()
	}
	t.Cleanup(func() { pageRetryPause = previous })
	return &pauses
}

// imagePages serves one image page per cursor, after failing reads of the
// cursors in failures as many times as listed there.
func imagePages(t *testing.T, failures map[string][]int) (*fakeServer, *[]string) {
	t.Helper()
	var mu sync.Mutex
	cursors := []string{}
	pages := map[string]map[string]any{
		"":   {"images": []any{imageFixture("a", "ready"), imageFixture("b", "ready")}, "next_cursor": "p2"},
		"p2": {"images": []any{imageFixture("c", "ready")}, "next_cursor": "p3"},
		"p3": {"images": []any{imageFixture("d", "ready")}},
	}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /images/search": func(w http.ResponseWriter, r *http.Request) {
			cursor := r.URL.Query().Get("cursor")
			mu.Lock()
			cursors = append(cursors, cursor)
			var status int
			if queue := failures[cursor]; len(queue) > 0 {
				status, failures[cursor] = queue[0], queue[1:]
			}
			mu.Unlock()
			if status != 0 {
				w.Header().Set("Content-Type", "text/html")
				w.WriteHeader(status)
				if _, err := io.WriteString(w, "<html>bad gateway</html>"); err != nil {
					t.Error(err)
				}
				return
			}
			writeJSON(t, w, pages[cursor])
		},
	})
	return fake, &cursors
}

func collectImages(t *testing.T, client *Client, cursor string) ([]string, []error) {
	t.Helper()
	var ids []string
	var failures []error
	for image, err := range client.Images().ListAll(t.Context(), &ListImagesOptions{Cursor: cursor}) {
		if err != nil {
			failures = append(failures, err)
			continue
		}
		ids = append(ids, image.ID)
	}
	return ids, failures
}

func TestListAll_whenAPageFailsTransiently(t *testing.T) {
	// Given a second page that fails twice with gateway errors.
	pauses := recordPageRetryPauses(t)
	fake, cursors := imagePages(t, map[string][]int{"p2": {502, 503}})
	client := newTestClient(t, fake.Server)
	// When walking every page.
	ids, failures := collectImages(t, client, "")
	// Then the page is read again after a jittered pause and the walk completes.
	require.Empty(t, failures)
	require.Equal(t, []string{"a", "b", "c", "d"}, ids)
	require.Equal(t, []string{"", "p2", "p2", "p2", "p3"}, *cursors)
	require.Len(t, *pauses, 2)
	require.InDelta(t, 450*time.Millisecond, (*pauses)[0], float64(50*time.Millisecond))
	require.InDelta(t, 900*time.Millisecond, (*pauses)[1], float64(100*time.Millisecond))
}

func TestListAll_whenAPageKeepsFailingResumesAtItsCursor(t *testing.T) {
	// Given a second page that fails on all three reads.
	pauses := recordPageRetryPauses(t)
	fake, cursors := imagePages(t, map[string][]int{"p2": {502, 502, 502}})
	client := newTestClient(t, fake.Server)
	// When walking every page, then resuming from the error's PageCursor.
	ids, failures := collectImages(t, client, "")
	require.Len(t, failures, 1)
	var detail *Error
	require.ErrorAs(t, failures[0], &detail)
	resumed, resumeFailures := collectImages(t, client, detail.PageCursor)
	// Then the error names the failed page and the resumed walk skips and repeats nothing.
	require.ErrorIs(t, failures[0], ErrInternalServer)
	require.Equal(t, 502, detail.StatusCode)
	require.Equal(t, "p2", detail.PageCursor)
	require.Empty(t, resumeFailures)
	require.Equal(t, []string{"a", "b", "c", "d"}, append(ids, resumed...))
	require.Equal(t, []string{"", "p2", "p2", "p2", "p2", "p3"}, *cursors)
	require.Len(t, *pauses, 2)
}

func TestListAll_whenTheFirstPageFailsPermanently(t *testing.T) {
	// Given a first page refused with 403.
	pauses := recordPageRetryPauses(t)
	fake, cursors := imagePages(t, map[string][]int{"": {403}})
	client := newTestClient(t, fake.Server)
	// When walking.
	ids, failures := collectImages(t, client, "")
	// Then it is read once and the error carries the empty first-page cursor.
	require.Empty(t, ids)
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], ErrForbidden)
	var detail *Error
	require.ErrorAs(t, failures[0], &detail)
	require.Empty(t, detail.PageCursor)
	require.Equal(t, []string{""}, *cursors)
	require.Empty(t, *pauses)
}

func TestListAll_whenCancelledDuringARetryPause(t *testing.T) {
	// Given a page that fails transiently and a caller that cancels during the pause.
	fake, cursors := imagePages(t, map[string][]int{"": {503}})
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	previous := pageRetryPause
	pageRetryPause = func(ctx context.Context, delay time.Duration) error {
		cancel()
		return contextUploadPause(ctx, delay)
	}
	t.Cleanup(func() { pageRetryPause = previous })
	// When walking.
	var failures []error
	for _, err := range client.Images().ListAll(ctx, nil) {
		failures = append(failures, err)
	}
	// Then the walk ends with the cancellation, still naming the failed page.
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], context.Canceled)
	require.Equal(t, []string{""}, *cursors)
}

func TestPageRetryDelay(t *testing.T) {
	previous := pageRetryJitter
	pageRetryJitter = func() float64 { return 1 }
	t.Cleanup(func() { pageRetryJitter = previous })
	status := func(code int, retryAfter time.Duration) *Error {
		err := newSDKError("X", code, "x")
		err.Source = SourceAPI
		err.RetryAfter = retryAfter
		return err
	}
	limit := status(429, 0)
	limit.Code = CodeLimitExceeded
	for name, tc := range map[string]struct {
		err   error
		retry int
		delay time.Duration
		ok    bool
	}{
		"connection":         {transportError(context.Background(), errors.New("reset")), 0, 500 * time.Millisecond, true},
		"502":                {status(502, 0), 1, time.Second, true},
		"429 with a hint":    {status(429, 3*time.Second), 0, 3 * time.Second, true},
		"503 hint over 10s":  {status(503, 11*time.Second), 0, 0, false},
		"third read":         {status(502, 0), 2, 0, false},
		"400":                {status(400, 0), 0, 0, false},
		"500":                {status(500, 0), 0, 0, false},
		"organization limit": {limit, 0, 0, false},
		"client timeout":     {transportError(context.Background(), context.DeadlineExceeded), 0, 0, false},
		"not an SDK error":   {errors.New("x"), 0, 0, false},
	} {
		t.Run(name, func(t *testing.T) {
			delay, ok := pageRetryDelay(tc.err, tc.retry)
			require.Equal(t, tc.ok, ok)
			require.Equal(t, tc.delay, delay)
		})
	}
}

func TestListAll_whenConcurrentWalksShareAFailedTokenExchange(t *testing.T) {
	// Given two walks whose shared token exchange fails with one error.
	var exchanges atomic.Int32
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /auth/token": func(w http.ResponseWriter, _ *http.Request) {
			exchanges.Add(1)
			time.Sleep(100 * time.Millisecond)
			w.WriteHeader(http.StatusInternalServerError)
		},
	})
	client := newTestClient(t, fake.Server, WithJWTToken("jwt"), WithProjectID("p"))
	// When both walks run at once.
	results := make([]error, 2)
	var wg sync.WaitGroup
	for index, cursor := range []string{"cursor-A", "cursor-B"} {
		wg.Go(func() {
			for _, err := range client.Images().ListAll(t.Context(), &ListImagesOptions{Cursor: cursor}) {
				results[index] = err
			}
		})
	}
	wg.Wait()
	// Then each walk's error names its own page, and the shared error is not changed.
	var first, second *Error
	require.ErrorAs(t, results[0], &first)
	require.ErrorAs(t, results[1], &second)
	require.Equal(t, "cursor-A", first.PageCursor)
	require.Equal(t, "cursor-B", second.PageCursor)
	require.NotSame(t, first, second)
	require.ErrorIs(t, results[0], ErrInternalServer)
	require.EqualValues(t, 1, exchanges.Load())
}

func TestListAll_whenCancelledBetweenPages(t *testing.T) {
	// Given a walk whose caller cancels after the first page's last item.
	fake, cursors := imagePages(t, nil)
	client := newTestClient(t, fake.Server)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	// When cancelling once the first page is consumed.
	var ids []string
	var failures []error
	for image, err := range client.Images().ListAll(ctx, nil) {
		if err != nil {
			failures = append(failures, err)
			continue
		}
		ids = append(ids, image.ID)
		if image.ID == "b" {
			cancel()
		}
	}
	// Then the cancellation names the next page, an exact resume point.
	require.Equal(t, []string{"a", "b"}, ids)
	require.Len(t, failures, 1)
	require.ErrorIs(t, failures[0], context.Canceled)
	var detail *Error
	require.ErrorAs(t, failures[0], &detail)
	require.Equal(t, "p2", detail.PageCursor)
	require.Equal(t, []string{""}, *cursors)
}

func TestListAll_whenTheRetryAfterHeaderAsksForAPause(t *testing.T) {
	for name, tc := range map[string]struct {
		header   string
		requests int
		pauses   []time.Duration
	}{
		"under 10 s is waited for": {"3", 2, []time.Duration{3 * time.Second}},
		"over 10 s ends the walk":  {"30", 1, []time.Duration{}},
	} {
		t.Run(name, func(t *testing.T) {
			// Given a first page answered 429 with only a Retry-After header.
			pauses := recordPageRetryPauses(t)
			var requests atomic.Int32
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /images/search": func(w http.ResponseWriter, _ *http.Request) {
					if requests.Add(1) == 1 {
						w.Header().Set("Retry-After", tc.header)
						w.WriteHeader(http.StatusTooManyRequests)
						return
					}
					writeJSON(t, w, map[string]any{"images": []any{}})
				},
			})
			client := newTestClient(t, fake.Server)
			// When walking.
			_, failures := collectImages(t, client, "")
			// Then the hint decides the pause, or ends the walk at once.
			require.EqualValues(t, tc.requests, requests.Load())
			require.Equal(t, tc.pauses, *pauses)
			if tc.requests == 1 {
				require.Len(t, failures, 1)
				require.ErrorIs(t, failures[0], ErrRateLimit)
			} else {
				require.Empty(t, failures)
			}
		})
	}
}
