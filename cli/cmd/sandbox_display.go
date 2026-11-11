// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strconv"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/workload"
)

func sandboxListTable(items []gen.SandboxListItem) output.Table {
	rows := make([][]string, 0, len(items))
	for _, item := range items {
		rows = append(rows, []string{item.Id, item.Name, string(item.Class), item.Status, item.RegionId, sandboxArtifactSummary(item.Image, item.Snapshot, item.ImageRef), formatTimePtr(item.CreatedAt)})
	}
	return output.Table{Headers: []string{"ID", "NAME", "CLASS", "STATUS", "REGION", "ARTIFACT", "CREATED"}, Rows: rows}
}

// sandboxUnsized labels macOS resource sizes that are not available before first boot.
const sandboxUnsized = "sizing on first boot"

// formatCores prints a CPU amount in cores (1 core = 2 vCPUs) in its shortest
// exact form: 2, 0.5, 1.5.
func formatCores(cores float64) string {
	return strconv.FormatFloat(cores, 'f', -1, 64)
}

func sandboxDetailKV(detail gen.Sandbox) [][2]string {
	cpu := formatCores(detail.Cpu)
	coresMax := formatCores(detail.CoresMax)
	memory := strconv.Itoa(int(detail.MemoryMib))
	if detail.Class == workload.ClassMacOS && detail.CoresMax == 0 {
		cpu, coresMax, memory = sandboxUnsized, sandboxUnsized, sandboxUnsized
	}
	kv := [][2]string{
		{"id", detail.Id},
		{"name", detail.Name},
		{"class", string(detail.Class)},
		{"status", detail.Status},
		{"region", detail.RegionId},
		{sandboxArtifactLabel(detail.Image, detail.Snapshot), sandboxArtifactSummary(detail.Image, detail.Snapshot, detail.ImageRef)},
		{"cpu", cpu},
		{"cores_max", coresMax},
		{"memory_mib", memory},
		{"disk_mib", strconv.Itoa(int(detail.DiskMib))},
	}
	if detail.Class == workload.ClassMacOS {
		persistent := ""
		if detail.Persistent != nil {
			persistent = strconv.FormatBool(*detail.Persistent)
		}
		kv = append(kv, [2]string{"persistent", persistent})
		reservation := "-"
		if detail.ReservationId != nil && *detail.ReservationId != "" {
			reservation = *detail.ReservationId
		}
		kv = append(kv, [2]string{"reservation_id", reservation})
	}
	if detail.MacBootMode != nil {
		kv = append(kv, [2]string{"mac_boot_mode", *detail.MacBootMode})
	}
	return kv
}

// The display label for the artifact the sandbox booted: "image" for an image,
// "snapshot" for a sandbox resumed from a memory snapshot.
func sandboxArtifactLabel(image *gen.ImageReference, snapshot *gen.SnapshotReference) string {
	if image == nil && snapshot != nil {
		return "snapshot"
	}
	return "image"
}

func sandboxArtifactSummary(image *gen.ImageReference, snapshot *gen.SnapshotReference, imageRef *string) string {
	if image != nil {
		if image.Name != nil && strings.TrimSpace(*image.Name) != "" {
			return *image.Name
		}
		return image.Id
	}
	if snapshot != nil {
		if snapshot.Name != nil && strings.TrimSpace(*snapshot.Name) != "" {
			return *snapshot.Name
		}
		return snapshot.Id
	}
	if imageRef != nil {
		return *imageRef
	}
	return ""
}
