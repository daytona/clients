// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSecretOptionsRedact_whenFormatted(t *testing.T) {
	for _, secret := range []string{"private-test-credential", ""} {
		credentials := GitCredentialOptions{
			Username: "git-user", Password: secret, Branch: "topic", Remote: "upstream", Timeout: time.Minute,
		}
		for _, tc := range []struct {
			name   string
			value  any
			fields []string
		}{
			{"clone", GitCloneOptions{
				Branch: "topic", CommitID: "commit-id", Depth: 2, InsecureSkipTLS: true,
				Username: "git-user", Password: secret, Timeout: time.Minute,
			}, []string{"Branch:topic", "CommitID:commit-id", "Depth:2", "InsecureSkipTLS:true", "Username:git-user", "Timeout:1m0s"}},
			{"credentials", credentials, []string{"Username:git-user", "Branch:topic", "Remote:upstream", "Timeout:1m0s"}},
			{
				"push",
				GitPushOptions{GitCredentialOptions: credentials, SetUpstream: true},
				[]string{"Username:git-user", "Branch:topic", "Remote:upstream", "Timeout:1m0s", "SetUpstream:true"},
			},
			{"get", GetSandboxOptions{ToolboxURL: "https://tools.example", AccessToken: secret}, []string{"ToolboxURL:https://tools.example"}},
		} {
			for _, format := range []string{"%v", "%+v", "%#v", "%s"} {
				t.Run(tc.name+"/"+format+"/"+map[bool]string{true: "empty", false: "set"}[secret == ""], func(t *testing.T) {
					// Given options carrying a caller-owned credential.
					// When formatted by the standard logging verbs.
					text := fmt.Sprintf(format, tc.value)
					// Then only the secret is masked, and all other fields remain visible.
					require.NotContains(t, text, "%!")
					secretField := "Password:"
					if tc.name == "get" {
						secretField = "AccessToken:"
					}
					require.Contains(t, text, secretField)
					if secret != "" {
						require.NotContains(t, text, secret)
						require.Contains(t, text, "***")
					} else {
						require.NotContains(t, text, "***")
					}
					for _, field := range tc.fields {
						require.Contains(t, text, field)
					}
				})
			}
		}
	}
}
