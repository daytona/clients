// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"math"
	"time"
)

// Limits returns organization limits and usage. API-key and platform-token
// credentials need read:limits; JWT authentication needs read:organization.
// With nil options or an empty OrganizationID, API-key authentication looks up
// and caches the key's organization; JWT authentication returns ErrBadRequest.
// A nil UsageLimit.Limit means unlimited, except on the deprecated sandbox
// total and storage counts, whose limit is not returned; a create that reaches
// one is still refused with ErrLimitExceeded (contact support to raise it). Invalid organization IDs and malformed
// responses return ErrBadRequest. Context cancellation remains reachable
// through errors.Is, including while waiting for a shared organization lookup.
// Cancelling one caller does not cancel that lookup for other callers;
// Client.Close and the request timeout bound the shared lookup itself.
func (c *Client) Limits(ctx context.Context, opts *GetLimitsOptions) (*OrganizationLimits, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, transportError(ctx, err)
	}
	orgID := ""
	if opts != nil {
		orgID = opts.OrganizationID
	}
	if orgID == "" {
		var err error
		orgID, err = c.accountOrganizationID(ctx)
		if err != nil {
			return nil, err
		}
	}
	segment, err := pathSegment(orgID)
	if err != nil {
		return nil, err
	}
	var raw json.RawMessage
	if err = c.api().do(ctx, apiRequest{Path: "/orgs/" + segment + "/limits", Result: &raw}); err != nil {
		return nil, err
	}
	return parseOrganizationLimits(raw)
}

func parseOrganizationLimits(raw json.RawMessage) (*OrganizationLimits, error) {
	object := accountResponse(raw, "organization limits")
	sandboxes := object.nested("sandboxes", false)
	builds := object.nested("image_builds", false)
	storage := object.nested("storage", false)
	result := &OrganizationLimits{
		Sandboxes: SandboxLimits{
			Concurrent: parseAccountUsage(sandboxes.nested("concurrent", false)),
			Total:      parseAccountUsage(sandboxes.nested("total", false)),
		},
		SandboxTimeout: parseSandboxTimeoutLimits(object.nested("sandbox_timeout", false)),
		ImageBuilds:    ImageBuildLimits{Concurrent: parseAccountUsage(builds.nested("concurrent", false))},
		Storage: StorageLimits{
			Volumes:   parseAccountUsage(storage.nested("volumes", false)),
			Images:    parseAccountUsage(storage.nested("images", false)),
			Snapshots: parseAccountUsage(storage.nested("snapshots", false)),
		},
		SecretsPerProject:      parseAccountUsage(object.nested("secrets_per_project", false)),
		APIKeysPerProject:      parseAccountUsage(object.nested("api_keys_per_project", false)),
		APIKeysPerOrganization: parseAccountUsage(object.nested("api_keys_per_organization", false)),
	}
	if resources := object.nested("sandbox_resources", true); resources != nil {
		result.SandboxResources = &SandboxResourceLimits{
			MaxCores: resources.number("max_cores"), MaxMemoryMiB: resources.integer("max_memory_mib"),
		}
	}
	if reservations := object.nested("mac_reservations", true); reservations != nil {
		value := parseAccountUsage(reservations)
		result.MacReservations = &value
	}
	if members := object.nested("members", true); members != nil {
		result.Members = &MemberLimits{
			UsageLimit: parseAccountUsage(members), PendingInvitations: members.integer("pending_invitations"),
		}
	}
	if *object.err != nil {
		return nil, *object.err
	}
	return result, nil
}

func parseSandboxTimeoutLimits(object *accountObject) SandboxTimeoutLimits {
	var result SandboxTimeoutLimits
	for _, entry := range []struct {
		name   string
		target *time.Duration
	}{{"max_seconds", &result.Max}, {"ceiling_seconds", &result.Ceiling}} {
		seconds := object.integer(entry.name)
		if seconds > math.MaxInt64/int64(time.Second) || seconds < math.MinInt64/int64(time.Second) {
			object.fail(entry.name, badRequest("value overflows time.Duration"))
			continue
		}
		*entry.target = time.Duration(seconds) * time.Second
	}
	return result
}

func parseAccountUsage(object *accountObject) UsageLimit {
	return UsageLimit{Limit: object.optionalInteger("limit", true), Usage: object.integer("usage")}
}
