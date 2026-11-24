// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona_test

import (
	"fmt"

	daytona "go.daytona.com/sdk/v2"
)

func ExampleCPUArch() {
	// The same architecture configures both sandbox creation and image builds.
	create := daytona.CreateOptions{CPUArch: daytona.CPUArchARM64}
	build := daytona.BuildImageOptions{CPUArch: create.CPUArch}
	fmt.Println(create.CPUArch, build.CPUArch)
	// Output: arm64 arm64
}
