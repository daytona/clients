// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"runtime/debug"
	"strings"
)

func sdkVersion() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return "unknown"
	}
	return versionFromBuildInfo(info)
}

func versionFromBuildInfo(info *debug.BuildInfo) string {
	const module = "go.daytona.com/sdk/v2"
	if info.Main.Path == module && info.Main.Version != "" {
		return strings.TrimPrefix(info.Main.Version, "v")
	}
	for _, dep := range info.Deps {
		if dep.Path == module {
			if dep.Replace != nil {
				if dep.Replace.Version != "" {
					return strings.TrimPrefix(dep.Replace.Version, "v")
				}
				return "(devel)"
			}
			if dep.Version != "" {
				return strings.TrimPrefix(dep.Version, "v")
			}
		}
	}
	return "unknown"
}
