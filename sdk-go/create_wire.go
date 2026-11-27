// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// The create routes share these wire fields, but their public option types
// deliberately expose only the fields supported by each source.
type createBody struct {
	Name          string               `json:"name,omitempty"`
	Image         string               `json:"image,omitempty"`
	Snapshot      string               `json:"snapshot,omitempty"`
	Labels        *map[string]string   `json:"labels,omitempty"`
	Ports         *[]createPort        `json:"ports,omitempty"`
	Region        string               `json:"region_id,omitempty"`
	Lifecycle     *lifecyclePolicyWire `json:"lifecycle_policy,omitempty"`
	WaitSeconds   int64                `json:"wait_seconds"`
	CreateTimeout *int64               `json:"create_timeout_seconds,omitempty"`
	// Key is sent as the Idempotency-Key header only, as in Python and TypeScript.
	Key         string             `json:"-"`
	Env         *map[string]string `json:"env,omitempty"`
	Volumes     *[]createVolume    `json:"volumes,omitempty"`
	Secrets     *[]string          `json:"secrets,omitempty"`
	CPU         float64            `json:"cpu,omitempty"`
	CPUMax      *float64           `json:"cores_max,omitempty"`
	Memory      int64              `json:"memory_mib,omitempty"`
	MemoryMax   *int64             `json:"memory_max_mib,omitempty"`
	Disk        int64              `json:"disk_mib,omitempty"`
	DiskMax     *int64             `json:"disk_max_mib,omitempty"`
	CPUArch     CPUArch            `json:"cpu_arch,omitempty"`
	CPUType     string             `json:"cpu_type,omitempty"`
	Spot        *bool              `json:"spot,omitempty"`
	User        string             `json:"user,omitempty"`
	Network     JSONValue          `json:"network_policy,omitempty"`
	Cores       int                `json:"cores,omitempty"`
	Persistent  *bool              `json:"persistent,omitempty"`
	Reservation string             `json:"reservation,omitempty"`
	AutoRenew   *bool              `json:"auto_renew,omitempty"`
}

type createPort struct {
	Port     int    `json:"port"`
	Protocol string `json:"proto"`
}

type createVolume struct {
	Volume    string `json:"volume"`
	MountPath string `json:"mount_path"`
	Subpath   string `json:"subpath,omitempty"`
	ReadOnly  bool   `json:"read_only,omitempty"`
}

type preparedCreate struct {
	path  string
	body  createBody
	class SandboxClass
	wait  time.Duration
}
