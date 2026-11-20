// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"strings"
	"time"
)

type sandboxObservation struct {
	info       SandboxInfo
	toolboxURL string
}

func parseSandbox(data json.RawMessage) (sandboxObservation, error) {
	object, err := parseObject(data, "sandbox")
	if err != nil {
		return sandboxObservation{}, err
	}
	var result sandboxObservation
	if fieldErr := sandboxStrings(object, &result); fieldErr != nil {
		return result, fieldErr
	}
	if strings.TrimSpace(result.info.ID) == "" {
		return result, badRequest("sandbox response is missing its id")
	}
	if strings.TrimSpace(result.info.RawClass) == "" {
		return result, badRequest("sandbox response is missing its class")
	}
	result.info.State = sandboxState(result.info.RawState)
	switch result.info.RawClass {
	case "linux":
		result.info.Class = SandboxClassLinux
	case "macos":
		result.info.Class = SandboxClassMacOS
	default:
		result.info.Class = SandboxClassUnknown
	}
	if result.info.Region == "" {
		result.info.Region = regionOf(result.info.ID)
	}
	if fieldErr := sandboxResources(object, &result.info.Resources); fieldErr != nil {
		return result, fieldErr
	}
	if fieldErr := sandboxOptionalFields(object, &result.info); fieldErr != nil {
		return result, fieldErr
	}
	if fieldErr := sandboxTimestamps(object, &result.info); fieldErr != nil {
		return result, fieldErr
	}
	return result, nil
}

func sandboxStrings(object jsonObject, result *sandboxObservation) error {
	fields := []struct {
		name     string
		target   *string
		required bool
	}{
		{"id", &result.info.ID, true},
		{"class", &result.info.RawClass, true},
		{"name", &result.info.Name, true},
		{"status", &result.info.RawState, true},
		{"region_id", &result.info.Region, true},
		{"error", &result.info.Error, false},
		{"user", &result.info.User, false},
		{"forked_from", &result.info.ForkedFrom, false},
		{"reservation_id", &result.info.ReservationID, false},
		{"image_build_id", &result.info.ImageBuildID, false},
		{"toolbox_proxy_url", &result.toolboxURL, false},
	}
	for _, entry := range fields {
		value, err := field[string](object, entry.name, entry.required)
		if err != nil {
			return err
		}
		*entry.target = value
	}
	return nil
}

func sandboxResources(object jsonObject, resources *Resources) error {
	cpu, err := field[float64](object, "cpu", true)
	if err != nil {
		return err
	}
	cpuMax, err := field[float64](object, "cores_max", true)
	if err != nil {
		return err
	}
	resources.CPU, resources.CPUMax = cpu, &cpuMax
	memory, err := integerField(object, "memory_mib", true)
	if err != nil {
		return err
	}
	disk, err := integerField(object, "disk_mib", true)
	if err != nil {
		return err
	}
	resources.MemoryGiB, resources.DiskGiB = float64(memory)/1024, float64(disk)/1024
	for name, target := range map[string]**float64{
		"memory_max_mib": &resources.MemoryMaxGiB, "disk_max_mib": &resources.DiskMaxGiB,
	} {
		if !present(object[name]) {
			continue
		}
		value, parseErr := integerField(object, name, false)
		if parseErr != nil {
			return parseErr
		}
		gib := float64(value) / 1024
		*target = &gib
	}
	return nil
}

func sandboxTimestamps(object jsonObject, info *SandboxInfo) error {
	for name, target := range map[string]*time.Time{
		"created_at": &info.CreatedAt, "updated_at": &info.UpdatedAt, "started_at": &info.StartedAt,
		"stopped_at": &info.StoppedAt, "last_activity_at": &info.LastActivityAt,
		"create_timeout_at": &info.CreateTimeoutAt, "spot_evicted_at": &info.SpotEvictedAt,
	} {
		value, err := timestampField(object, name)
		if err != nil {
			return err
		}
		*target = value
	}
	return nil
}

func timestampField(object jsonObject, name string) (time.Time, error) {
	if !present(object[name]) {
		return time.Time{}, nil
	}
	value, err := field[string](object, name, true)
	if err != nil {
		return time.Time{}, err
	}
	stamp, err := parseTimestamp(value)
	if err != nil {
		return time.Time{}, badRequest("response field " + name + " is not RFC 3339")
	}
	return stamp, nil
}

func parseTimestamp(value string) (time.Time, error) {
	// time.Parse also accepts single-digit hours, comma fractions and ±24:00.
	if len(value) < 20 || value[13] != ':' || value[16] != ':' || strings.Contains(value, ",") {
		return time.Time{}, badRequest("invalid RFC 3339 timestamp")
	}
	upper := strings.ToUpper(value)
	if !strings.HasSuffix(upper, "Z") {
		zone := upper[len(upper)-6:]
		if zone[1:3] > "23" || zone[4:6] > "59" {
			return time.Time{}, badRequest("invalid RFC 3339 timezone")
		}
	}
	stamp, err := time.Parse(time.RFC3339Nano, upper)
	if err != nil || stamp.Year() < 1 {
		return time.Time{}, badRequest("invalid RFC 3339 timestamp")
	}
	return stamp, nil
}
