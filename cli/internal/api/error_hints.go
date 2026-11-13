// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

func hintFor(ae *APIError) string {
	switch {
	case ae.Status == http.StatusUnauthorized:
		return "credential rejected — run `daytona login`, or check --api-key / DAYTONA_API_KEY"
	case ae.Status == http.StatusForbidden:
		if ae.Code == CodeFeatureNotEnabled {
			if feature, ok := ae.Body["feature"].(string); ok && feature != "" {
				return fmt.Sprintf("the %s feature is not enabled for this organization", feature)
			}
			return "this feature is not enabled for this organization"
		}
		if ae.Code == CodeEntitlementRequired {
			if entitlement, ok := ae.Body["entitlement"].(string); ok && entitlement != "" {
				return fmt.Sprintf("this organization lacks the required entitlement %s", entitlement)
			}
			return "this organization lacks the required entitlement for that action"
		}
		if ae.Code == CodeSignupNotInvited {
			// The signup refusal names what to do; a credential hint
			// would misdiagnose a first login that was never provisioned.
			return ""
		}
		if ae.Code == CodeUserSuspended {
			// The account was deprovisioned by its organization's identity
			// provider; the message says so, and no credential fixes it.
			return ""
		}
		if path, _, _ := strings.Cut(ae.Path, "?"); ae.Method == http.MethodPost && strings.HasSuffix(path, "/signed-preview-urls") {
			return "signed preview links are refused for this credential; for a URL with no credential run `daytona sandbox port expose <sandbox-id> <port> --public`, or send the token from `daytona sandbox port preview-url` in the X-Daytona-Preview-Token header"
		}
		return "this credential is not allowed to do that here"
	case ae.Status == http.StatusNotImplemented && ae.Code == CodeSearchNotConfigured:
		return "sandbox search is not enabled; use `sandbox list` (with --region to narrow) instead"
	case ae.Status == http.StatusServiceUnavailable && ae.Code == CodeSearchUnavailable:
		return "sandbox search is temporarily unavailable; retry, or use `sandbox list`"
	case ae.Status == http.StatusNotImplemented && ae.Code == CodeBillingDisabled:
		return "billing is disabled on this deployment"
	case ae.Status == http.StatusBadGateway && ae.Code == CodeBillingUnreachable:
		return "the billing service is temporarily unreachable; retry later"
	case ae.Status == http.StatusPaymentRequired && ae.Code == CodeProjectBudgetExceeded:
		return "this project reached its monthly budget — `daytona project budget get` (needs a `daytona login` session) shows the budget and this month's spend; raise or remove it to start sandboxes again"
	case ae.Status == http.StatusNotFound && strings.HasPrefix(ae.Path, "/invitations/"):
		// The token routes: no id, no org context —
		// a uniform 404 covers unknown, used, declined, expired and revoked.
		return "the invitation is unknown, already accepted or declined, expired, or revoked — ask the organization to invite you again"
	case ae.Status == http.StatusNotFound:
		if ae.Code == CodeImageNotFound {
			if ae.Method == http.MethodPost && (ae.Path == "/sandboxes" || ae.Path == "/sandboxes/from-snapshot" || ae.Path == "/sandboxes/macos") {
				return "create takes an image name or img_… id; `daytona image list` shows available images, and `daytona image create NAME --ref REF` imports a registry image"
			}
			return "check the image name or id and the selected --project; `daytona image list` shows available images"
		}
		return "check the id and the selected --org/--project; a resource in another project also answers 404"
	case ae.Status == http.StatusConflict && ae.Code == CodeNotSupportedForClass:
		return "this sandbox class does not support that operation — see the server message for the class-specific alternative"
	case ae.Status == http.StatusConflict:
		return ""
	case ae.Status == http.StatusTooManyRequests && ae.Code == CodeLimitExceeded:
		limit, _ := ae.Body["limit"].(string)
		cur, max := ae.Body["current"], ae.Body["max"]
		if limit == "max_mac_reservations" {
			currentCount, currentOK := intExtra(ae.Body, "current")
			maxCount, maxOK := intExtra(ae.Body, "max")
			if currentOK && maxOK {
				if currentCount >= maxCount {
					return fmt.Sprintf("organization limit max_mac_reservations reached (%d of %d) — a Mac reservation frees at the end of its period: let an active one lapse with `daytona reservation update RESERVATION_ID --auto-renew=false`, or upgrade the plan; `daytona org limits` shows the current usage", currentCount, maxCount)
				}
				remaining := maxCount - currentCount
				fit := "Mac reservations fit"
				if remaining == 1 {
					fit = "Mac reservation fits"
				}
				// Only a bulk purchase is refused below the cap, and only it
				// takes --count and --min-count.
				if path, _, _ := strings.Cut(ae.Path, "?"); strings.HasSuffix(path, "/reservations/bulk") {
					return fmt.Sprintf("only %d more %s under the organization limit (%d of %d held) — lower --count or set --min-count", remaining, fit, currentCount, maxCount)
				}
				return fmt.Sprintf("only %d more %s under the organization limit (%d of %d held)", remaining, fit, currentCount, maxCount)
			}
		}
		if limit != "" && cur != nil && max != nil {
			hint := fmt.Sprintf("organization limit %s reached (%v of %v) — %s or ask for a higher limit", limit, cur, max, limitRemedy(limit))
			if limitShownByOrgLimits(limit) {
				hint += "; `daytona org limits` shows the current usage"
			}
			return hint
		}
		return "an organization limit is reached — `daytona org limits` shows the current usage"
	case ae.Status == http.StatusTooManyRequests && ae.Code == CodeOverloaded:
		if retryAfter, ok := intExtra(ae.Body, "retry_after_seconds"); ok {
			return fmt.Sprintf("the service is busy — retry in %ds", retryAfter)
		}
		return "the service is busy — retry shortly"
	case ae.Status == http.StatusTooManyRequests && ae.Code == CodeRateLimited:
		if retryAfter, ok := intExtra(ae.Body, "retry_after_seconds"); ok {
			return fmt.Sprintf("rate limited — retry in %ds", retryAfter)
		}
		return "rate limited — retry shortly"
	case ae.Status == http.StatusTooManyRequests:
		return "rate limited — retry shortly"
	}
	if ae.TraceID != "" {
		return "trace id " + ae.TraceID + " (quote it in support requests)"
	}
	return ""
}

// limitRemedy suggests how to free capacity for documented limit names.
// Unknown limits receive generic resource-cleanup guidance.
func limitRemedy(limit string) string {
	switch limit {
	case "max_concurrent_sandboxes":
		return "stop or delete sandboxes"
	case "max_members":
		return "remove members or revoke pending invitations"
	default:
		return "delete the ones you no longer need"
	}
}

// limitShownByOrgLimits reports whether `daytona org limits` has a row for
// the limit. Only some per-project and per-organization limits are included.
func limitShownByOrgLimits(limit string) bool {
	switch limit {
	case "secrets_per_project", "api_keys_per_project", "api_keys_per_organization":
		return true
	}
	return strings.HasPrefix(limit, "max_")
}

func intExtra(body map[string]any, key string) (int64, bool) {
	v, ok := body[key]
	if !ok {
		return 0, false
	}
	switch n := v.(type) {
	case float64:
		return int64(n), true
	case int:
		return int64(n), true
	case int32:
		return int64(n), true
	case int64:
		return n, true
	case json.Number:
		parsed, err := n.Int64()
		if err != nil {
			return 0, false
		}
		return parsed, true
	default:
		return 0, false
	}
}
