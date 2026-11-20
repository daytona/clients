// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"time"
)

// SystemMetrics is one sandbox CPU, memory and disk usage observation.
type SystemMetrics struct {
	// Timestamp is when the sandbox sampled these metrics.
	Timestamp time.Time
	// CPUCount is the nonnegative number of CPUs.
	CPUCount int64
	// CPUUtilization is the fraction of CPU capacity in use, from zero to one.
	CPUUtilization float64
	// MemoryUsageBytes is used memory in bytes.
	MemoryUsageBytes int64
	// MemoryTotalBytes is total memory in bytes.
	MemoryTotalBytes int64
	// DiskUsageBytes is used disk capacity in bytes.
	DiskUsageBytes int64
	// DiskTotalBytes is total disk capacity in bytes.
	DiskTotalBytes int64
}

// Metrics reads current resource usage from the sandbox. A malformed sample,
// including negative byte counts or utilization outside 0..1, returns ErrBadRequest.
func (s *Sandbox) Metrics(ctx context.Context) (*SystemMetrics, error) {
	var raw JSONValue
	if err := s.toolbox().do(ctx, toolboxRequest{Path: "/system/metrics", Result: &raw}); err != nil {
		return nil, err
	}
	object, err := parseObject(raw, "system metrics")
	if err != nil {
		return nil, err
	}
	if _, stampErr := field[string](object, "timestamp", true); stampErr != nil {
		return nil, stampErr
	}
	result := &SystemMetrics{}
	result.Timestamp, err = timestampField(object, "timestamp")
	if err != nil {
		return nil, err
	}
	result.CPUUtilization, err = field[float64](object, "cpuUtilization", true)
	if err != nil {
		return nil, err
	}
	if result.CPUUtilization < 0 || result.CPUUtilization > 1 {
		return nil, badRequest("system metrics cpuUtilization must be in 0..1")
	}
	for name, target := range map[string]*int64{
		"cpuCount": &result.CPUCount, "memoryUsageBytes": &result.MemoryUsageBytes,
		"memoryTotalBytes": &result.MemoryTotalBytes, "diskUsageBytes": &result.DiskUsageBytes,
		"diskTotalBytes": &result.DiskTotalBytes,
	} {
		value, parseErr := integerField(object, name, true)
		if parseErr != nil {
			return nil, parseErr
		}
		if value < 0 {
			return nil, badRequest("system metrics " + name + " must be nonnegative")
		}
		*target = value
	}
	return result, nil
}
