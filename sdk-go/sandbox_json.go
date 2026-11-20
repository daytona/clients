// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// ToJSON returns an independent credential- and URL-free camelCase snapshot.
// Marshal it with encoding/json. This is a snapshot, not a way back: resume a
// stored sandbox with client.Get(ctx, json.SandboxID, nil).
func (s *Sandbox) ToJSON() SandboxHandleJSON {
	info := s.Info()
	result := SandboxHandleJSON{
		SandboxID: s.ID(), Name: knownJSONText(info.Name, info.jsonKnown.name),
		State: info.State, RawState: info.RawState, Region: info.Region,
		CPUMax: info.Resources.CPUMax, MemoryMax: info.Resources.MemoryMaxGiB, DiskMax: info.Resources.DiskMaxGiB,
		SandboxClass: info.Class, RawSandboxClass: info.RawClass, Persistent: info.Persistent, Spot: info.Spot,
		SpotEvictedAt:   snapshotTimestamp(info.SpotEvictedAt, info.spotEvictedAtWire),
		LifecyclePolicy: lifecycleJSON(info.LifecyclePolicy),
		ForkedFrom:      knownJSONText(info.ForkedFrom, info.jsonKnown.forkedFrom),
		ReservationID:   knownJSONText(info.ReservationID, info.jsonKnown.reservationID),
		User:            knownJSONText(info.User, info.jsonKnown.user),
		Error:           knownJSONText(info.Error, info.jsonKnown.error),
		ImageBuildID:    knownJSONText(info.ImageBuildID, info.jsonKnown.imageBuildID),
	}
	if info.State == SandboxStateUnknown && info.RawState == "" {
		result.State = ""
	}
	if info.Image != nil {
		result.Image = &SandboxArtifactJSON{
			ID: info.Image.ID, Name: knownJSONText(info.Image.Name, info.Image.nameKnown),
		}
	}
	if info.Snapshot != nil {
		result.Snapshot = &SandboxArtifactJSON{
			ID: info.Snapshot.ID, Name: knownJSONText(info.Snapshot.Name, info.Snapshot.nameKnown),
		}
	}
	if info.Labels != nil {
		result.Labels = &info.Labels
	}
	if info.jsonKnown.resources {
		result.CPU, result.Memory, result.Disk = &info.Resources.CPU, &info.Resources.MemoryGiB, &info.Resources.DiskGiB
	}
	return result
}

func knownJSONText(value string, known bool) *string {
	if !known && value == "" {
		return nil
	}
	return &value
}

func snapshotTimestamp(value time.Time, wire string) string {
	if wire != "" {
		return wire
	}
	if value.IsZero() {
		return ""
	}
	return value.Format(time.RFC3339Nano)
}

func lifecycleJSON(policy *LifecyclePolicy) *SandboxLifecycleJSON {
	if policy == nil {
		return nil
	}
	result := &SandboxLifecycleJSON{
		OnTimeout:          knownJSONText(string(policy.OnTimeout), policy.onTimeoutKnown),
		OnIdleTimeout:      knownJSONText(string(policy.OnIdleTimeout), policy.onIdleTimeoutKnown),
		TimeoutAt:          snapshotTimestamp(policy.TimeoutAt, policy.timeoutAtWire),
		IdleTimeoutAt:      snapshotTimestamp(policy.IdleTimeoutAt, policy.idleTimeoutAtWire),
		LastActivityAt:     snapshotTimestamp(policy.LastActivityAt, policy.lastActivityAtWire),
		LastActivitySource: knownJSONText(policy.LastActivitySource, policy.lastActivitySourceKnown),
	}
	if policy.Timeout != nil {
		seconds := int64(*policy.Timeout / time.Second)
		result.TimeoutSeconds = &seconds
	}
	if policy.IdleExpiry != nil {
		seconds := int64(*policy.IdleExpiry / time.Second)
		result.IdleExpirySeconds = &seconds
	}
	return result
}
