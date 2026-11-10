// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

type review2DeadlineTransport struct{}

func (review2DeadlineTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	<-req.Context().Done()
	return nil, req.Context().Err()
}

func TestCreateStreamProcess_timesOut_whenCreateBudgetExpires(t *testing.T) {
	for _, budget := range []time.Duration{time.Second, time.Minute} {
		t.Run(budget.String(), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given a create that cannot finish before its deadline.
				inner := api.New("https://toolbox.example", "fixture", "test")
				inner.HTTP.Transport = review2DeadlineTransport{}
				client := New(inner)
				ctx, cancel := context.WithTimeout(t.Context(), budget)
				defer cancel()
				start := time.Now()
				// When creating under the caller's budget.
				_, err := client.createStreamProcess(ctx, tbgen.CreateProcessRequest{}, nil)
				// Then the shorter budget wins and remains a timeout, not an interrupt.
				require.Equal(t, min(budget, 30*time.Second), time.Since(start))
				require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
				require.ErrorIs(t, err, context.DeadlineExceeded)
			})
		})
	}
}
