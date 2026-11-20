// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestImagePull_whenWaitDefaultsOrIsDisabled(t *testing.T) {
	wait := false
	for _, tt := range []struct {
		name  string
		opts  *PullImageOptions
		want  ImageStatus
		calls int
	}{
		{"default", nil, ImageStatusReady, 4},
		{"false", &PullImageOptions{Wait: &wait}, ImageStatusPending, 2},
		{"zero budget", &PullImageOptions{Timeout: 0}, ImageStatusReady, 4},
		{"forever", &PullImageOptions{Timeout: WaitForever}, ImageStatusReady, 4},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given a registry import with multiple early nonterminal observations.
			reads := 0
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
				"GET /images/img": func(w http.ResponseWriter, _ *http.Request) {
					status := []string{"pending", "building", "ready"}[min(reads, 2)]
					reads++
					writeJSON(t, w, imageFixture("img", status))
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, time.Millisecond)
				start := time.Now()
				// When pulling with the selected wait behavior.
				image, err := client.Images().Pull(t.Context(), "alpine:3", tt.opts)
				// Then the default reaches readiness; false returns after initial lookup.
				require.NoError(t, err)
				require.Equal(t, tt.want, image.Status)
				requests := fake.requests()
				require.Len(t, requests, tt.calls)
				require.JSONEq(t, `{"source":{"type":"registry","ref":"alpine:3"}}`, string(requests[0].Body))
				require.Empty(t, requests[1].Query)
				for _, request := range requests[2:] {
					require.Equal(t, "ready", request.Query.Get("wait"))
					require.Equal(t, "60000", request.Query.Get("timeout_ms"))
				}
				if tt.calls > 2 {
					require.Equal(t, time.Second, time.Since(start))
				}
			})
		})
	}
}

func TestImagePull_whenImportFails(t *testing.T) {
	for _, status := range []string{"failed", "error"} {
		for _, pending := range []bool{false, true} {
			t.Run(status+map[bool]string{false: "/initial", true: "/poll"}[pending], func(t *testing.T) {
				// Given an import failing at acceptance or after a readiness hold.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"POST /images": jsonHandler(t, map[string]string{"id": "img"}),
					"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
						current := status
						if pending && r.URL.Query().Get("wait") == "" {
							current = "pending"
						}
						wire := imageFixture("img", current)
						wire["error"] = "registry denied"
						writeJSON(t, w, wire)
					},
				})
				client := newTestClient(t, fake.Server)
				// When pulling with the default readiness wait.
				_, err := client.Images().Pull(t.Context(), "alpine:3", nil)
				// Then the structured failure keeps both identity and server explanation.
				var failure *ImageBuildFailedError
				require.ErrorAs(t, err, &failure)
				require.ErrorIs(t, err, ErrImageBuildFailed)
				require.Equal(t, "img", failure.ImageID)
				require.Equal(t, "registry denied", failure.BuildError)
				require.Equal(t, SourceSDK, failure.SDKError.Source)
			})
		}
	}
}

func TestImagePull_whenEachPhaseExhaustsTheTotalBudget(t *testing.T) {
	for _, phase := range []string{"submit", "lookup", "poll"} {
		t.Run(phase, func(t *testing.T) {
			// Given one stalled phase with otherwise immediate submission and lookup.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images": func(w http.ResponseWriter, r *http.Request) {
					if phase == "submit" {
						<-r.Context().Done()
						return
					}
					writeJSON(t, w, map[string]string{"id": "img"})
				},
				"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
					if phase == "lookup" || r.URL.Query().Has("wait") {
						<-r.Context().Done()
						return
					}
					writeJSON(t, w, imageFixture("img", "pending"))
				},
			})
			synctest.Test(t, func(t *testing.T) {
				client := imageClockClient(t, fake, 10*time.Second)
				start := time.Now()
				// When the pull budget expires in that phase.
				_, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Timeout: time.Second})
				// Then submission, lookup and readiness all share the same owning timeout.
				requireImageTimeout(t, err)
				require.Equal(t, time.Second, time.Since(start))
				require.Len(t, fake.requests(), map[string]int{"submit": 1, "lookup": 2, "poll": 3}[phase])
			})
		})
	}
}

func TestImagePull_whenCumulativeWorkExhaustsBudgetWithoutWait(t *testing.T) {
	// Given submission and lookup each fit separately, but not together.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images": func(w http.ResponseWriter, r *http.Request) {
			if imageDelay(r.Context(), 600*time.Millisecond) == nil {
				writeJSON(t, w, map[string]string{"id": "img"})
			}
		},
		"GET /images/img": func(w http.ResponseWriter, r *http.Request) {
			if imageDelay(r.Context(), 600*time.Millisecond) == nil {
				writeJSON(t, w, imageFixture("img", "ready"))
			}
		},
	})
	synctest.Test(t, func(t *testing.T) {
		client := imageClockClient(t, fake, 10*time.Second)
		wait := false
		start := time.Now()
		// When readiness waiting is disabled but a total pull budget is supplied.
		_, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Wait: &wait, Timeout: time.Second})
		// Then lookup cannot spend the submission's budget again.
		requireImageTimeout(t, err)
		require.Equal(t, time.Second, time.Since(start))
		require.Len(t, fake.requests(), 2)
	})
}

func TestImagePull_whenWaitDisabledReturnsFailedObservation(t *testing.T) {
	// Given an import already failed at the initial lookup.
	wire := imageFixture("img", "failed")
	wire["error"] = "registry denied"
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /images":    jsonHandler(t, map[string]string{"id": "img"}),
		"GET /images/img": jsonHandler(t, wire),
	})
	client := newTestClient(t, fake.Server)
	wait := false
	// When opting out of readiness waiting.
	image, err := client.Images().Pull(t.Context(), "alpine", &PullImageOptions{Wait: &wait})
	// Then the initial observation is returned without interpreting build failure.
	require.NoError(t, err)
	require.Equal(t, ImageStatusFailed, image.Status)
	require.Equal(t, "registry denied", image.Error)
	require.Len(t, fake.requests(), 2)
}

func TestImagePull_whenAcknowledgedIDIsMalformed(t *testing.T) {
	for _, body := range []any{
		nil,
		map[string]any{},
		map[string]any{"id": nil},
		map[string]any{"id": false},
		map[string]any{"id": " "},
		map[string]any{"id": "."},
		map[string]any{"id": ".."},
	} {
		t.Run("invalid id", func(t *testing.T) {
			// Given an import acknowledgement without a usable follow-up identity.
			fake := newFakeServer(t, map[string]http.HandlerFunc{"POST /images": jsonHandler(t, body)})
			client := newTestClient(t, fake.Server)
			// When submitting an import.
			_, err := client.Images().Pull(t.Context(), "alpine", nil)
			// Then malformed identities never reach a GET.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}
