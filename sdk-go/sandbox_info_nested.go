// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"maps"
	"math"
	"slices"
	"time"
)

func sandboxOptionalFields(object jsonObject, info *SandboxInfo) error {
	labels, err := field[map[string]json.RawMessage](object, "labels", false)
	if err != nil {
		return err
	}
	info.Labels = make(map[string]string, len(labels))
	for _, key := range slices.Sorted(maps.Keys(labels)) {
		value, parseErr := field[string](labels, key, true)
		if parseErr != nil {
			return badRequest("sandbox response has invalid labels." + key)
		}
		info.Labels[key] = value
	}
	mode, err := field[string](object, "mac_boot_mode", false)
	if err != nil {
		return err
	}
	info.MacBootMode = MacBootMode(mode)
	for _, entry := range []struct {
		name   string
		target **bool
	}{
		{"spot", &info.Spot}, {"persistent", &info.Persistent},
	} {
		if !present(object[entry.name]) {
			continue
		}
		value, parseErr := field[bool](object, entry.name, true)
		if parseErr != nil {
			return parseErr
		}
		*entry.target = &value
	}
	image, err := parseArtifact(object["image"])
	if err != nil {
		return err
	}
	info.Image = image
	snapshot, err := parseArtifact(object["snapshot"])
	if err != nil {
		return err
	}
	if snapshot != nil {
		info.Snapshot = &SnapshotRef{ID: snapshot.ID, Name: snapshot.Name}
	}
	info.LifecyclePolicy, err = parseLifecycle(object["lifecycle_policy"])
	if err != nil {
		return err
	}
	info.PortMap, err = parsePorts(object)
	return err
}

func parseArtifact(raw json.RawMessage) (*ImageRef, error) {
	if !present(raw) {
		return nil, nil //nolint:nilnil // An absent reference is a valid optional value, not a parse failure.
	} // Optional artifact references are absent on snapshot boots.
	object, err := parseObject(raw, "artifact")
	if err != nil {
		return nil, err
	}
	id, err := field[string](object, "id", true)
	if err != nil {
		return nil, err
	}
	name, err := field[string](object, "name", false)
	if err != nil {
		return nil, err
	}
	if id == "" {
		return nil, nil //nolint:nilnil // The sibling SDK drops references whose ID is empty.
	} // artifactRefOf drops an empty reference ID.
	return &ImageRef{ID: id, Name: name}, nil
}

func parsePorts(object jsonObject) ([]PortAssignment, error) {
	rows, err := field[[]json.RawMessage](object, "port_map", true)
	if err != nil {
		return nil, err
	}
	result := make([]PortAssignment, 0, len(rows))
	for _, row := range rows {
		port, parseErr := parseObject(row, "port_map item")
		if parseErr != nil {
			return nil, parseErr
		}
		host, hostErr := integerField(port, "host_port", true)
		if hostErr != nil {
			return nil, hostErr
		}
		if host < 1 || host > 65535 {
			return nil, badRequest("response field host_port must be within 1..65535")
		}
		vm, vmErr := integerField(port, "vm_port", true)
		if vmErr != nil {
			return nil, vmErr
		}
		if vm < 1 || vm > 65535 {
			return nil, badRequest("response field vm_port must be within 1..65535")
		}
		protocol, protocolErr := field[string](port, "proto", true)
		if protocolErr != nil {
			return nil, protocolErr
		}
		result = append(result, PortAssignment{HostPort: int(host), VMPort: int(vm), Protocol: protocol})
	}
	return result, nil
}

func parseLifecycle(raw json.RawMessage) (*LifecyclePolicy, error) {
	if !present(raw) {
		return nil, nil //nolint:nilnil // An absent lifecycle policy is valid and distinct from a zero-valued policy.
	}
	object, err := parseObject(raw, "lifecycle_policy")
	if err != nil {
		return nil, err
	}
	policy := &LifecyclePolicy{}
	// Ordered, so a response with several malformed fields always names the
	// same one.
	for _, entry := range []struct {
		name   string
		target **time.Duration
	}{
		{"timeout_seconds", &policy.Timeout}, {"idle_expiry_seconds", &policy.IdleExpiry},
	} {
		if !present(object[entry.name]) {
			continue
		}
		seconds, parseErr := integerField(object, entry.name, false)
		if parseErr != nil {
			return nil, parseErr
		}
		if seconds < math.MinInt32 || seconds > math.MaxInt32 {
			return nil, badRequest("response field lifecycle_policy." + entry.name + " must be an int32")
		}
		value := time.Duration(seconds) * time.Second
		*entry.target = &value
	}
	for _, entry := range []struct {
		name   string
		target *LifecycleAction
	}{
		{"on_timeout", &policy.OnTimeout}, {"on_idle_timeout", &policy.OnIdleTimeout},
	} {
		value, parseErr := field[string](object, entry.name, false)
		if parseErr != nil {
			return nil, parseErr
		}
		*entry.target = LifecycleAction(value)
	}
	for _, entry := range []struct {
		name   string
		target *time.Time
	}{
		{"timeout_at", &policy.TimeoutAt},
		{"idle_timeout_at", &policy.IdleTimeoutAt},
		{"last_activity_at", &policy.LastActivityAt},
	} {
		value, parseErr := timestampField(object, entry.name)
		if parseErr != nil {
			return nil, parseErr
		}
		*entry.target = value
	}
	policy.LastActivitySource, err = field[string](object, "last_activity_source", false)
	return policy, err
}
