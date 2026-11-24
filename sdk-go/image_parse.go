// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

func parseImage(raw JSONValue) (*Image, error) {
	object, err := parseObject(raw, "image")
	if err != nil {
		return nil, err
	}
	result := &Image{}
	for _, entry := range []struct {
		name   string
		target *string
	}{
		{"id", &result.ID},
		{"class", &result.RawClass},
		{"origin", &result.RawOrigin},
		{"region_id", &result.Region},
		{"retention", &result.RawRetention},
		{"status", &result.RawStatus},
		{"name", &result.Name},
		{"error", &result.Error},
	} {
		value, parseErr := field[string](object, entry.name, entry.name != "name" && entry.name != "error")
		if parseErr != nil {
			return nil, parseErr
		}
		*entry.target = value
	}
	for _, entry := range []struct {
		name   string
		target *bool
	}{
		{"anonymous", &result.Anonymous},
		{"format_outdated", &result.FormatOutdated},
		{"platform", &result.Platform},
		{"default", &result.Default},
		{"deprecated", &result.Deprecated},
	} {
		value, parseErr := field[bool](object, entry.name, true)
		if parseErr != nil {
			return nil, parseErr
		}
		*entry.target = value
	}
	result.AvailableIn, err = artifactRegions(object)
	if err != nil {
		return nil, err
	}
	if err = artifactTimes(object, &result.CreatedAt, &result.UpdatedAt); err != nil {
		return nil, err
	}
	result.ExpiresAt, err = timestampField(object, "expires_at")
	if err != nil {
		return nil, err
	}
	result.FormatVersion, err = integerField(object, "format_version", true)
	if err != nil {
		return nil, err
	}
	if err = imageOptionalFields(object, result); err != nil {
		return nil, err
	}
	result.Status = imageStatus(result.RawStatus)
	result.Class = imageClass(result.RawClass)
	result.Origin = imageOrigin(result.RawOrigin)
	result.Retention = imageRetention(result.RawRetention)
	return result, nil
}

func imageOptionalFields(object jsonObject, result *Image) error {
	for _, entry := range []struct {
		name   string
		target **int64
	}{
		{"bytes", &result.Bytes},
		{"layer_count", &result.LayerCount},
	} {
		value, err := artifactOptionalInteger(object, entry.name)
		if err != nil {
			return err
		}
		*entry.target = value
	}
	disk, err := artifactOptionalInteger(object, "disk_mib")
	if err != nil {
		return err
	}
	if disk != nil {
		gib := float64(*disk) / 1024
		result.DiskGiB = &gib
	}
	if present(object["source"]) {
		result.Source, err = parseImageSource(object["source"])
	}
	return err
}

func artifactOptionalInteger(object jsonObject, name string) (*int64, error) {
	if !present(object[name]) {
		return nil, nil //nolint:nilnil // An absent optional integer has no value or error.
	}
	value, err := integerField(object, name, true)
	if err != nil {
		return nil, err
	}
	return &value, nil
}

func artifactRegions(object jsonObject) ([]string, error) {
	entries, err := field[[]JSONValue](object, "region_ids", true)
	if err != nil {
		return nil, err
	}
	regions := make([]string, 0, len(entries))
	for _, entry := range entries {
		value, parseErr := field[string](jsonObject{"region_ids": entry}, "region_ids", true)
		if parseErr != nil {
			return nil, parseErr
		}
		regions = append(regions, value)
	}
	return regions, nil
}

func artifactTimes(object jsonObject, created, updated *time.Time) error {
	for _, entry := range []struct {
		name   string
		target *time.Time
	}{
		{"created_at", created},
		{"updated_at", updated},
	} {
		if !present(object[entry.name]) {
			return badRequest("response field " + entry.name + " is required")
		}
		value, err := timestampField(object, entry.name)
		if err != nil {
			return err
		}
		*entry.target = value
	}
	return nil
}

func imageStatus(raw string) ImageStatus {
	switch raw {
	case "pending":
		return ImageStatusPending
	case "building":
		return ImageStatusBuilding
	case "ready":
		return ImageStatusReady
	case "failed":
		return ImageStatusFailed
	case "error":
		return ImageStatusError
	default:
		return ImageStatusUnknown
	}
}

func imageClass(raw string) ImageClass {
	switch raw {
	case "linux":
		return ImageClassLinux
	case "macos":
		return ImageClassMacOS
	case "windows":
		return ImageClassWindows
	default:
		return ImageClassUnknown
	}
}

func imageOrigin(raw string) ImageOrigin {
	switch raw {
	case "registry":
		return ImageOriginRegistry
	case "dockerfile":
		return ImageOriginDockerfile
	case "capture":
		return ImageOriginCapture
	case "windows":
		return ImageOriginWindows
	case "macos":
		return ImageOriginMacOS
	default:
		return ImageOriginUnknown
	}
}

func imageRetention(raw string) ImageRetention {
	switch raw {
	case "permanent":
		return ImageRetentionPermanent
	case "idle":
		return ImageRetentionIdle
	default:
		return ImageRetentionUnknown
	}
}
