// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
)

type sandboxPage struct {
	observations []sandboxObservation
	cursor       string
	total        int64
	relation     string
}

func parseSandboxPage(raw JSONValue, search bool) (sandboxPage, error) {
	var page sandboxPage
	object, err := parseObject(raw, "sandbox page")
	if err != nil {
		return page, err
	}
	rows, err := field[[]JSONValue](object, "sandboxes", true)
	if err != nil {
		return page, err
	}
	page.cursor, err = field[string](object, "next_cursor", false)
	if err != nil {
		return page, err
	}
	if search {
		page.total, err = integerField(object, "total", true)
		if err != nil {
			return page, err
		}
		if page.total < 0 {
			return page, badRequest("search response has a negative total")
		}
		page.relation, err = field[string](object, "total_relation", true)
	}
	if !search {
		camel, parseErr := field[string](object, "nextCursor", false)
		if parseErr != nil {
			return page, parseErr
		}
		if !present(object["next_cursor"]) {
			page.cursor = camel
		}
	}
	if err != nil {
		return page, err
	}
	page.observations = make([]sandboxObservation, 0, len(rows))
	for _, row := range rows {
		observation, parseErr := parseSandbox(row)
		if parseErr != nil {
			return page, parseErr
		}
		page.observations = append(page.observations, observation)
	}
	return page, nil
}

// Missing toolbox URLs trigger bounded detail reads. An unreadable detail does
// not discard a valid list item; cancellation/closure still stops the page.
func (c *Client) pageHandles(ctx context.Context, observations []sandboxObservation) ([]*Sandbox, error) {
	items := make([]*Sandbox, len(observations))
	failures := make([]error, len(observations))
	var next atomic.Int64
	var workers sync.WaitGroup
	for range min(8, len(observations)) {
		workers.Go(func() {
			for {
				index := int(next.Add(1)) - 1
				if index >= len(observations) {
					return
				}
				items[index], failures[index] = c.pageHandle(ctx, observations[index])
			}
		})
	}
	workers.Wait()
	for _, err := range failures {
		if err != nil {
			return nil, err
		}
	}
	return items, nil
}

func (c *Client) pageHandle(ctx context.Context, observation sandboxObservation) (*Sandbox, error) {
	source := observation
	if source.toolboxURL == "" {
		detail, err := c.getSandbox(ctx, source.info.ID)
		switch {
		case err == nil:
			source = detail
		case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded),
			errors.Is(err, ErrClientClosed):
			return nil, err
		}
	}
	return c.newSandbox(sandboxInit{Info: source.info, ToolboxURL: source.toolboxURL})
}
