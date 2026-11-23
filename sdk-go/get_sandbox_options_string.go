// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "fmt"

// String prints all get options with a non-empty AccessToken redacted.
func (o GetSandboxOptions) String() string {
	type plain GetSandboxOptions
	value := plain(o)
	if value.AccessToken != "" {
		value.AccessToken = "***"
	}
	return fmt.Sprintf("%+v", value)
}

// GoString prints get options with the same redaction as String.
func (o GetSandboxOptions) GoString() string { return o.String() }
