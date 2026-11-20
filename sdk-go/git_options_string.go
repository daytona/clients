// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "fmt"

// String prints all clone options with a non-empty Password redacted.
func (o GitCloneOptions) String() string {
	type plain GitCloneOptions
	value := plain(o)
	if value.Password != "" {
		value.Password = "***"
	}
	return fmt.Sprintf("%+v", value)
}

// GoString prints clone options with the same redaction as String.
func (o GitCloneOptions) GoString() string { return o.String() }

// String prints all credential options with a non-empty Password redacted.
func (o GitCredentialOptions) String() string {
	type plain GitCredentialOptions
	value := plain(o)
	if value.Password != "" {
		value.Password = "***"
	}
	return fmt.Sprintf("%+v", value)
}

// GoString prints credential options with the same redaction as String.
func (o GitCredentialOptions) GoString() string { return o.String() }

// String prints all push options with a non-empty Password redacted.
func (o GitPushOptions) String() string {
	return fmt.Sprintf("{GitCredentialOptions:%s SetUpstream:%t}", o.GitCredentialOptions, o.SetUpstream)
}

// GoString prints push options with the same redaction as String.
func (o GitPushOptions) GoString() string { return o.String() }
