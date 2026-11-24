// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/coder/websocket"

	"go.daytona.com/sdk/v2/internal/events"
)

func (c *Client) connectSandboxEvents(ctx context.Context, region string) (events.Connection, error) {
	segment, err := pathSegment(region)
	if err != nil {
		return events.Connection{Permanent: true}, err
	}
	var ticket struct {
		Ticket string `json:"ticket"`
	}
	err = c.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/events/" + segment + "/ticket", Result: &ticket, Timeout: 30 * time.Second,
	})
	if err != nil {
		return eventsFailure(err), err
	}
	if ticket.Ticket == "" {
		return events.Connection{}, badRequest("events ticket response is missing ticket")
	}
	request, err := c.api().makeRequest(ctx, apiRequest{Path: "/events/" + segment})
	if err != nil {
		return events.Connection{}, err
	}
	// Only the one-use ticket authenticates the upgrade; never send the bearer.
	dialCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	conn, response, err := websocket.Dial(dialCtx, request.URL.String(), &websocket.DialOptions{
		HTTPClient: c.http, HTTPHeader: request.Header,
		Subprotocols: []string{events.Subprotocol, "daytona.ticket." + ticket.Ticket},
	})
	if err != nil {
		failure := transportError(ctx, err)
		if response != nil {
			// Dial retains at most 1024 bytes and already closes the network body.
			body, readErr := io.ReadAll(response.Body)
			closeErr := response.Body.Close()
			failure = errorFromResponse(response, body)
			if readErr != nil || closeErr != nil {
				failure = errors.Join(failure, readErr, closeErr)
			}
		}
		policy := eventsFailure(failure)
		// An expired/consumed upgrade ticket gets a fresh mint, unlike mint auth.
		if response != nil && response.StatusCode == http.StatusUnauthorized {
			policy.Permanent = false
		}
		return policy, failure
	}
	if conn.Subprotocol() != events.Subprotocol {
		return events.Connection{}, errors.Join(badRequest("unexpected events subprotocol"), conn.CloseNow())
	}
	return events.Connection{Socket: conn}, nil
}

func eventsFailure(err error) events.Connection {
	var detail *Error
	if !errors.As(err, &detail) {
		return events.Connection{}
	}
	permanent := detail.StatusCode == http.StatusNotFound ||
		detail.StatusCode == http.StatusUnauthorized || detail.StatusCode == http.StatusForbidden ||
		(detail.StatusCode == http.StatusNotImplemented && detail.Code == CodeWorkloadPlaneElsewhere)
	return events.Connection{Permanent: permanent, RetryAfter: detail.RetryAfter}
}
