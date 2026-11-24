// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"strconv"
)

// AuditActionGroup lists the audit actions available for a target type.
type AuditActionGroup struct {
	// TargetType is the open server-defined audit target type.
	TargetType string
	// Actions contains the open server-defined action names.
	Actions []string
}

// AuditActionList describes the audit log's supported target types and actions.
type AuditActionList struct {
	// Targets groups actions by target type.
	Targets []AuditActionGroup
}

// ListAuditActions returns all supported audit actions in one response.
// Malformed responses return ErrBadRequest; cancellation remains reachable
// through errors.Is.
func (c *Client) ListAuditActions(ctx context.Context) (*AuditActionList, error) {
	var raw json.RawMessage
	if err := c.api().do(ctx, apiRequest{Path: "/audit/actions", Result: &raw}); err != nil {
		return nil, err
	}
	object := accountResponse(raw, "audit actions")
	items := object.array("targets")
	result := &AuditActionList{Targets: make([]AuditActionGroup, len(items))}
	for i, item := range items {
		group := object.objectItem("targets["+strconv.Itoa(i)+"]", item)
		result.Targets[i] = AuditActionGroup{
			TargetType: group.text("target_type", true),
			Actions:    group.strings("actions"),
		}
	}
	if *object.err != nil {
		return nil, *object.err
	}
	return result, nil
}
