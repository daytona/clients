// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

func Ref[T any](value T) *T {
	return &value
}
