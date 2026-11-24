// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

func parseSnapshot(raw JSONValue) (*Snapshot, error) {
	object, err := parseObject(raw, "snapshot")
	if err != nil {
		return nil, err
	}
	result := &Snapshot{}
	for name, target := range map[string]*string{
		"id": &result.ID, "name": &result.Name, "class": &result.RawClass, "kind": &result.RawKind,
		"region_id": &result.Region, "status": &result.RawStatus,
		"error": &result.Error, "sandbox_id": &result.SandboxID,
	} {
		value, parseErr := field[string](object, name, name != "error" && name != "sandbox_id")
		if parseErr != nil {
			return nil, parseErr
		}
		*target = value
	}
	result.AvailableIn, err = artifactRegions(object)
	if err != nil {
		return nil, err
	}
	if err = artifactTimes(object, &result.CreatedAt, &result.UpdatedAt); err != nil {
		return nil, err
	}
	for name, target := range map[string]**int64{"bytes": &result.Bytes, "memory_bytes": &result.MemoryBytes} {
		value, parseErr := artifactOptionalInteger(object, name)
		if parseErr != nil {
			return nil, parseErr
		}
		*target = value
	}
	switch result.RawClass {
	case "linux":
		result.Class = SnapshotClassLinux
	default:
		result.Class = SnapshotClassUnknown
	}
	switch result.RawKind {
	case "full":
		result.Kind = SnapshotKindFull
	default:
		result.Kind = SnapshotKindUnknown
	}
	result.Status = snapshotStatus(result.RawStatus)
	return result, nil
}

func snapshotStatus(raw string) SnapshotStatus {
	switch raw {
	case "pending":
		return SnapshotStatusPending
	case "creating":
		return SnapshotStatusCreating
	case "ready":
		return SnapshotStatusReady
	case "failed":
		return SnapshotStatusFailed
	case "error":
		return SnapshotStatusError
	default:
		return SnapshotStatusUnknown
	}
}
