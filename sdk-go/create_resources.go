// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "math"

func prepareLinuxResources(body *createBody, resources *Resources) error {
	if resources == nil {
		return nil
	}
	for _, axis := range []struct {
		name string
		cpu  float64
		set  bool
	}{
		{"CPU", resources.CPU, resources.CPU != 0},
		{"CPUMax", valueOrZero(resources.CPUMax), resources.CPUMax != nil},
	} {
		if !axis.set {
			continue
		}
		if err := createCPU(axis.name, axis.cpu); err != nil {
			return err
		}
	}
	body.CPU, body.CPUMax = resources.CPU, clonePointer(resources.CPUMax)
	for _, axis := range []struct {
		name   string
		gib    float64
		set    bool
		target *int64
	}{
		{"MemoryGiB", resources.MemoryGiB, resources.MemoryGiB != 0, &body.Memory},
		{"DiskGiB", resources.DiskGiB, resources.DiskGiB != 0, &body.Disk},
	} {
		if !axis.set {
			continue
		}
		mib, err := createMiB(axis.gib, 1, axis.name)
		if err != nil {
			return err
		}
		*axis.target = mib
	}
	for _, axis := range []struct {
		name   string
		gib    *float64
		target **int64
	}{
		{"MemoryMaxGiB", resources.MemoryMaxGiB, &body.MemoryMax},
		{"DiskMaxGiB", resources.DiskMaxGiB, &body.DiskMax},
	} {
		if axis.gib == nil {
			continue
		}
		mib, err := createMiB(*axis.gib, 1, axis.name)
		if err != nil {
			return err
		}
		*axis.target = &mib
	}
	return nil
}

func createCPU(name string, cores float64) error {
	if math.IsNaN(cores) || math.IsInf(cores, 0) || cores <= 0 {
		return badRequest("Resources." + name + " must be positive finite cores")
	}
	if name == "CPUMax" && (math.IsInf(cores*2, 0) || math.Trunc(cores*2) != cores*2) {
		return badRequest("Resources.CPUMax must be a multiple of 0.5 cores")
	}
	return nil
}

func prepareMacOSResources(body *createBody, resources *MacOSResources) error {
	if resources == nil {
		return nil
	}
	if resources.CPU != 0 && (resources.CPU < 4 || resources.CPU > math.MaxInt32) {
		return badRequest("Resources.CPU must be an integer in 4..2147483647")
	}
	body.Cores = resources.CPU
	for _, axis := range []struct {
		name   string
		gib    float64
		floor  int64
		target *int64
	}{
		{"MemoryGiB", resources.MemoryGiB, 4096, &body.Memory},
		{"DiskGiB", resources.DiskGiB, 81920, &body.Disk},
	} {
		if axis.gib == 0 {
			continue
		}
		mib, err := createMiB(axis.gib, axis.floor, axis.name)
		if err != nil {
			return err
		}
		*axis.target = mib
	}
	return nil
}

func createMiB(gib float64, floor int64, name string) (int64, error) {
	mib := gib * 1024
	if math.IsNaN(mib) || math.IsInf(mib, 0) || math.Trunc(mib) != mib || mib < float64(floor) || mib > math.MaxInt32 {
		return 0, badRequest("Resources." + name + " must convert to whole MiB within its supported range")
	}
	return int64(mib), nil
}

func valueOrZero(value *float64) float64 {
	if value == nil {
		return 0
	}
	return *value
}
