// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "runtime/debug"

func sdkVersion() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "unknown"
	}
	const module = "go.daytona.com/sdk/v2"
	if info.Main.Path == module && info.Main.Version != "" {
		return info.Main.Version
	}
	for _, dep := range info.Deps {
		if dep.Path == module {
			if dep.Replace != nil {
				if dep.Replace.Version != "" {
					return dep.Replace.Version
				}
				return "(devel)"
			}
			if dep.Version != "" {
				return dep.Version
			}
		}
	}
	return "unknown"
}
