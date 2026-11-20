// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
	"go.daytona.com/sdk/v2/internal/events"
)

func TestEventsObserveStoppedSandbox(t *testing.T) {
	// Given: use the production internal hub, not an exported-for-test SDK hook.
	client := newClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-events-stop")
	body, err := json.Marshal(map[string]any{
		"name": name, "persistent": true, "wait_seconds": 60,
		"labels": map[string]string{labelKey: labelValue, "name": name},
	})
	require.NoError(t, err)
	var created struct {
		ID string `json:"id"`
	}
	require.NoError(t, rawAPI(ctx, http.MethodPost, "/sandboxes", body, &created))
	id := created.ID
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		require.NoError(t, rawAPI(cleanupCtx, http.MethodDelete, "/sandboxes/"+id, nil, nil))
	})
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	hub := events.New(ctx, events.Config[events.Update]{
		Connect: liveEventsConnection, Observe: func(update events.Update) events.Update { return update },
	})
	t.Cleanup(hub.Close)
	ch, release := hub.Watch(ctx, id, sandbox.Info().Region)
	defer release()
	require.NotNil(t, ch)
	for {
		update := liveEventsUpdate(t, ctx, ch)
		if update.Status == "running" {
			break
		}
		require.NotContains(t, []string{"failed", "lost", "deleted"}, update.Status)
	}

	// When: stop independently of lifecycle code, through the real HTTP API.
	require.NoError(t, rawAPI(ctx, http.MethodPost, "/sandboxes/"+id+"/stop", nil, nil))

	// Then: only socket observations, with no polling, can satisfy this check.
	for {
		update := liveEventsUpdate(t, ctx, ch)
		if update.Status == "stopped" {
			break
		}
		require.NotContains(t, []string{"failed", "lost", "deleted"}, update.Status)
	}
	t.Log("events hub received running snapshot and stopped observation after raw stop POST")
}

func TestEventsObserveDeletedSandbox(t *testing.T) {
	// Given: rawSandbox intentionally creates a non-persistent sandbox.
	client := newClient(t)
	ctx := testContext(t)
	id := rawSandbox(t, uniqueName(t, "gosdk-events-delete"))
	sandbox, err := client.Get(ctx, id, nil)
	require.NoError(t, err)
	hub := events.New(ctx, events.Config[events.Update]{
		Connect: liveEventsConnection, Observe: func(update events.Update) events.Update { return update },
	})
	t.Cleanup(hub.Close)
	ch, release := hub.Watch(ctx, id, sandbox.Info().Region)
	defer release()
	require.NotNil(t, ch)
	require.Equal(t, "running", liveEventsUpdate(t, ctx, ch).Status)

	// When: deletion is the supported lifecycle transition for this fixture.
	require.NoError(t, rawAPI(ctx, http.MethodDelete, "/sandboxes/"+id, nil, nil))

	// Then
	for {
		update := liveEventsUpdate(t, ctx, ch)
		if update.Status == "deleted" {
			break
		}
		require.NotContains(t, []string{"failed", "lost"}, update.Status)
	}
	t.Log("events hub received running snapshot and deleted observation after raw DELETE")
}

func liveEventsConnection(ctx context.Context, region string) (events.Connection, error) {
	var ticket struct {
		Ticket string `json:"ticket"`
	}
	if err := rawAPI(ctx, http.MethodPost, "/events/"+region+"/ticket", nil, &ticket); err != nil {
		return events.Connection{Permanent: true}, err
	}
	base := strings.TrimRight(os.Getenv("DAYTONA_V2_API_URL"), "/")
	if base == "" {
		base = daytona.DefaultAPIURL
	}
	httpClient := &http.Client{
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
	}
	openCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	//nolint:bodyclose // websocket.Dial owns and closes its HTTP response body.
	conn, _, err := websocket.Dial(openCtx, base+"/events/"+region, &websocket.DialOptions{
		HTTPClient: httpClient, Subprotocols: []string{events.Subprotocol, "daytona.ticket." + ticket.Ticket},
	})
	if err != nil {
		return events.Connection{}, err
	}
	if conn.Subprotocol() != events.Subprotocol {
		return events.Connection{}, errors.Join(errors.New("unexpected events subprotocol"), conn.CloseNow())
	}
	return events.Connection{Socket: conn}, nil
}

func liveEventsUpdate(t *testing.T, ctx context.Context, ch <-chan events.Update) events.Update {
	t.Helper()
	select {
	case update, ok := <-ch:
		require.True(t, ok, "events unavailable; live check must not pass by polling")
		return update
	case <-ctx.Done():
		t.Fatal(ctx.Err())
		return events.Update{}
	}
}
