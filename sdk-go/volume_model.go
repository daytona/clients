// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// VolumeState is a volume's observed status; RawState preserves unknown values.
type VolumeState string

const (
	// VolumeStateUnknown denotes an unrecognized server state.
	VolumeStateUnknown VolumeState = "unknown"
	// VolumeStateReady means the volume is usable.
	VolumeStateReady VolumeState = "ready"
	// VolumeStateDeleting means deletion is in progress.
	VolumeStateDeleting VolumeState = "deleting"
	// VolumeStateDeleted means deletion has completed.
	VolumeStateDeleted VolumeState = "deleted"
	// VolumeStateError means the volume failed; see Volume.Error.
	VolumeStateError VolumeState = "error"
)

// VolumeType identifies a volume's storage implementation.
type VolumeType string

const (
	// VolumeTypeUnknown denotes an unrecognized server type.
	VolumeTypeUnknown VolumeType = "unknown"
	// VolumeTypeDisk is a persistent disk volume.
	VolumeTypeDisk VolumeType = "disk"
	// VolumeTypeBucket is a bucket-backed volume.
	VolumeTypeBucket VolumeType = "bucket"
)

// Volume is caller-owned metadata for a persistent project volume.
type Volume struct {
	// ID is the stable volume identifier.
	ID string
	// Name is the project-unique name.
	Name string
	// Type is the normalized storage type.
	Type VolumeType
	// RawType is the server storage type verbatim.
	RawType string
	// State is the normalized server status.
	State VolumeState
	// RawState is the server status verbatim.
	RawState string
	// SizeGiB is disk capacity in GiB; nil means not reported or not applicable.
	SizeGiB *int64
	// Error is the recorded failure explanation.
	Error string
	// CreatedAt is the creation timestamp.
	CreatedAt time.Time
	// UpdatedAt is the metadata update timestamp.
	UpdatedAt time.Time
	// LastUsedAt is zero before the first attachment or when unreported.
	LastUsedAt time.Time
	// Options contains type-specific JSON options; nil means absent.
	Options map[string]JSONValue
	// UsedBytes is logical file usage in bytes; nil means unreported.
	UsedBytes *int64
	// UsedBytesAt is zero when the usage observation timestamp is absent.
	UsedBytesAt time.Time
}

func parseVolume(raw JSONValue) (Volume, error) {
	var result Volume
	object, err := parseObject(raw, "volume")
	if err != nil {
		return result, err
	}
	for _, entry := range []struct {
		name     string
		target   *string
		required bool
	}{
		{"id", &result.ID, true},
		{"name", &result.Name, true},
		{"type", &result.RawType, true},
		{"status", &result.RawState, true},
		{"error", &result.Error, false},
	} {
		value, parseErr := field[string](object, entry.name, entry.required)
		if parseErr != nil {
			return result, parseErr
		}
		*entry.target = value
	}
	result.Type = volumeType(result.RawType)
	result.State = volumeState(result.RawState)
	for _, entry := range []struct {
		name     string
		target   *time.Time
		required bool
	}{
		{"created_at", &result.CreatedAt, true},
		{"updated_at", &result.UpdatedAt, true},
		{"last_used_at", &result.LastUsedAt, false},
		{"used_bytes_at", &result.UsedBytesAt, false},
	} {
		if entry.required && !present(object[entry.name]) {
			return result, badRequest("response field " + entry.name + " is required")
		}
		stamp, parseErr := timestampField(object, entry.name)
		if parseErr != nil {
			return result, parseErr
		}
		*entry.target = stamp
	}
	for name, target := range map[string]**int64{"size_gib": &result.SizeGiB, "used_bytes": &result.UsedBytes} {
		if present(object[name]) {
			value, parseErr := integerField(object, name, true)
			if parseErr != nil {
				return result, parseErr
			}
			*target = &value
		}
	}
	result.Options, err = field[map[string]JSONValue](object, "options", false)
	if err != nil {
		return result, err
	}
	return result, nil
}

func volumeState(raw string) VolumeState {
	switch raw {
	case "ready":
		return VolumeStateReady
	case "deleting":
		return VolumeStateDeleting
	case "deleted":
		return VolumeStateDeleted
	case "error":
		return VolumeStateError
	default:
		return VolumeStateUnknown
	}
}

func volumeType(raw string) VolumeType {
	switch raw {
	case "disk":
		return VolumeTypeDisk
	case "bucket":
		return VolumeTypeBucket
	default:
		return VolumeTypeUnknown
	}
}

// volumeResourcePage parses only the resource envelope at the boundary; its
// mapper owns item validation. The same shape is shared by secrets and volumes.
func volumeResourcePage[T any](raw JSONValue, key string, parse func(JSONValue) (T, error)) (*Page[T], error) {
	object, err := parseObject(raw, key+" page")
	if err != nil {
		return nil, err
	}
	rows, err := field[[]JSONValue](object, key, true)
	if err != nil {
		return nil, err
	}
	cursor, err := field[string](object, "next_cursor", false)
	if err != nil {
		return nil, err
	}
	result := &Page[T]{Items: make([]T, 0, len(rows)), NextCursor: cursor}
	for _, row := range rows {
		item, parseErr := parse(row)
		if parseErr != nil {
			return nil, parseErr
		}
		result.Items = append(result.Items, item)
	}
	return result, nil
}
