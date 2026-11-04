// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import "testing"

// Vectors shared with the SDKs: TS strictUriEncode / Python quote(safe="").
func TestStrictPathSegment_matchesSDKEncoders(t *testing.T) {
	cases := map[string]string{
		"7fJk2mQpXv9dLc3RtY": "7fJk2mQpXv9dLc3RtY",
		"a-b.c_d~e":          "a-b.c_d~e",
		"id with space":      "id%20with%20space",
		"a/b":                "a%2Fb",
		"a!b'c(d)e*f":        "a%21b%27c%28d%29e%2Af",
		"x:y@z&w=v+u$t,s;r":  "x%3Ay%40z%26w%3Dv%2Bu%24t%2Cs%3Br",
		"\u00e9":             "%C3%A9",
	}
	for in, want := range cases {
		if got := strictPathSegment(in); got != want {
			t.Errorf("%q: got %q want %q", in, got, want)
		}
	}
}
