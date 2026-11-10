// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestHintForImageNotFound_suggestsDiscovery_whenGettingOrDeleting(t *testing.T) {
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		t.Run(method, func(t *testing.T) {
			// Given an image reference that does not exist in this project.
			failure := &APIError{Status: http.StatusNotFound, Code: CodeImageNotFound, Method: method, Path: "/images/missing"}
			// When selecting a remedy.
			hint := hintFor(failure)
			// Then discovery is suggested, not importing a new resource.
			require.Contains(t, hint, "daytona image list")
			require.NotContains(t, hint, "daytona image create")
		})
	}
}

// The invitation token routes carry no id and no org context, so the generic
// 404 hint ("check the id and --org/--project") would mislead; they get the
// sentence the dashboard shows instead.
func TestHintFor404OnInvitationTokenRoutes(t *testing.T) {
	for _, path := range []string{"/invitations/lookup", "/invitations/accept", "/invitations/decline"} {
		h := hintFor(&APIError{Status: http.StatusNotFound, Code: "NOT_FOUND", Method: "POST", Path: path})
		if !strings.Contains(h, "ask the organization to invite you again") || strings.Contains(h, "--org") {
			t.Errorf("%s: unexpected hint %q", path, h)
		}
	}
	h := hintFor(&APIError{Status: http.StatusNotFound, Code: "NOT_FOUND", Method: "GET", Path: "/orgs/x/invitations"})
	if !strings.Contains(h, "--org/--project") {
		t.Errorf("org-side route keeps the generic hint, got %q", h)
	}
}

func TestHintFor_suspendedAccountHasNoCredentialHint(t *testing.T) {
	// USER_SUSPENDED's message says the account was deprovisioned; the
	// generic 403 hint would send the person to rotate a credential.
	h := hintFor(&APIError{Status: http.StatusForbidden, Code: CodeUserSuspended, Method: "GET", Path: "/orgs"})
	if h != "" {
		t.Fatalf("expected no hint for a suspended account, got %q", h)
	}
}

func TestHintFor_invitationRequiredHasNoCredentialHint(t *testing.T) {
	// SIGNUP_NOT_INVITED names what to do in its message; the generic 403
	// hint would blame a credential a never-provisioned user does not have.
	h := hintFor(&APIError{Status: http.StatusForbidden, Code: CodeSignupNotInvited, Method: "GET", Path: "/me"})
	if h != "" {
		t.Fatalf("expected no credential hint when an invitation is required, got %q", h)
	}
}

func TestHintForLimitExceededNamesWhatFreesTheLimit(t *testing.T) {
	cases := []struct {
		limit, remedy string
		orgLimitsRow  bool
	}{
		{"max_concurrent_sandboxes", "stop or delete sandboxes", true},
		{"max_members", "remove members or revoke pending invitations", true},
		{"secrets_per_project", "delete the ones you no longer need", true},
		{"example_limit", "delete the ones you no longer need", false},
	}
	for _, c := range cases {
		h := hintFor(&APIError{Status: http.StatusTooManyRequests, Code: CodeLimitExceeded,
			Body: map[string]any{"limit": c.limit, "current": float64(1), "max": float64(1)}})
		want := "organization limit " + c.limit + " reached (1 of 1) — " + c.remedy + " or ask for a higher limit"
		if c.orgLimitsRow {
			want += "; `daytona org limits` shows the current usage"
		}
		if h != want {
			t.Errorf("%s: hint = %q, want %q", c.limit, h, want)
		}
	}
}

func TestHintForFeatureNotEnabled(t *testing.T) {
	named := hintFor(&APIError{Status: http.StatusForbidden, Code: CodeFeatureNotEnabled, Body: map[string]any{"feature": "example_feature"}})
	if named != "the example_feature feature is not enabled for this organization" {
		t.Fatalf("named hint = %q", named)
	}
	bare := hintFor(&APIError{Status: http.StatusForbidden, Code: CodeFeatureNotEnabled})
	if bare != "this feature is not enabled for this organization" {
		t.Fatalf("bare hint = %q", bare)
	}
}
