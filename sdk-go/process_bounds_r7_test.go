// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestProcessListRejectsPID_whenOutsideWireRange(t *testing.T) {
	for _, value := range []int64{-1, math.MaxInt32 + 1, math.MaxInt64} {
		t.Run(strconv.FormatInt(value, 10), func(t *testing.T) {
			if value > int64(math.MaxInt) {
				t.Skip("PID cannot represent this value on this architecture")
			}
			// Given a PID outside the toolbox's non-negative int32 range.
			pid := int(value)
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"GET /processes": jsonHandler(t, map[string][]ProcessInfo{"items": {}}),
			})
			// When listing processes.
			_, err := sandbox.Process().List(t.Context(), &ListProcessesOptions{PID: &pid})
			// Then validation names the option before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "PID")
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, SourceSDK, sdkErr.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestProcessListSendsPID_whenAtWireBoundaries(t *testing.T) {
	for _, pid := range []int{0, math.MaxInt32} {
		t.Run(strconv.Itoa(pid), func(t *testing.T) {
			// Given a boundary PID accepted by the toolbox.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"GET /processes": jsonHandler(t, map[string][]ProcessInfo{"items": {}}),
			})
			// When filtering the process list.
			_, err := sandbox.Process().List(t.Context(), &ListProcessesOptions{PID: &pid})
			// Then the explicit PID, including zero, reaches the wire unchanged.
			require.NoError(t, err)
			require.Equal(t, strconv.Itoa(pid), fake.requests()[0].Query.Get("pid"))
		})
	}
}

func TestProcessKillRejectsEscalation_whenRoundedDelayExceedsMaximum(t *testing.T) {
	for _, delay := range []time.Duration{
		-time.Nanosecond, 10*time.Minute + time.Nanosecond, 600001 * time.Millisecond, time.Duration(math.MaxInt64),
	} {
		t.Run(delay.String(), func(t *testing.T) {
			// Given an escalation delay outside the server's 0..600000 ms range.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes/p/signals": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
			})
			handle, err := sandbox.Process().Connect("p")
			require.NoError(t, err)
			// When signaling with escalation.
			err = handle.Kill(t.Context(), &KillProcessOptions{EscalateAfter: &delay})
			// Then invalid durations fail locally with the public option name.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "EscalateAfter")
			var sdkErr *Error
			require.ErrorAs(t, err, &sdkErr)
			require.Equal(t, SourceSDK, sdkErr.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestProcessKillSendsEscalation_whenRoundedDelayWithinBounds(t *testing.T) {
	for _, tc := range []struct {
		delay time.Duration
		ms    int64
	}{
		{0, 0}, {time.Nanosecond, 1}, {10*time.Minute - time.Nanosecond, 600000}, {10 * time.Minute, 600000},
	} {
		t.Run(tc.delay.String(), func(t *testing.T) {
			// Given an escalation delay at a rounding boundary.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
				"POST /processes/p/signals": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
			})
			handle, err := sandbox.Process().Connect("p")
			require.NoError(t, err)
			// When requesting escalation.
			err = handle.Kill(t.Context(), &KillProcessOptions{EscalateAfter: &tc.delay})
			// Then the rounded wire delay, including explicit zero, is preserved.
			require.NoError(t, err)
			require.JSONEq(t, `{"signal":"SIGTERM","escalateAfterMs":`+strconv.FormatInt(tc.ms, 10)+`,"escalateTo":"SIGKILL"}`,
				string(fake.requests()[0].Body))
		})
	}
}
