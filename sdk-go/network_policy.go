// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "slices"

// NetworkPolicySpec is a network-policy request, without a read-side version.
// A domain allow-list implies blocking all other egress unless BlockAll is set.
type NetworkPolicySpec struct {
	// BlockAll blocks all egress except the allow-lists; nil uses server defaults.
	BlockAll *bool
	// AllowCIDRs allows these CIDRs. Nil omits the field; an empty slice sends [].
	// Nonempty CIDRs require BlockAll=true or a non-null domain allow-list.
	AllowCIDRs []string
	// AllowDomains allows these domains. A nil pointer omits the field; a pointer
	// to a nil slice sends null; a pointer to an empty slice sends [].
	// A non-null domain list cannot be combined with BlockAll=false.
	AllowDomains *[]string
}

// NetworkPolicy is a versioned policy reported by the API.
type NetworkPolicy struct {
	// Version is the policy schema version.
	Version int64
	// BlockAll reports whether unspecified egress is blocked.
	BlockAll bool
	// AllowCIDRs is the permitted CIDR list.
	AllowCIDRs []string
	// AllowDomains is nil when the domain allow-list is absent.
	AllowDomains []string
}

// NetworkPolicyInfo contains desired and applied policy state. Updates apply
// asynchronously. ETag is required for the next conditional update or reset.
type NetworkPolicyInfo struct {
	// Desired is nil when the sandbox uses the default policy.
	Desired *NetworkPolicy
	// DesiredRevision is the requested revision.
	DesiredRevision int64
	// ETag is the opaque concurrency token, including its quotes.
	ETag string
	// Applied is nil when no sandbox-specific policy is applied.
	Applied *NetworkPolicy
	// AppliedRevision is nil until an applied revision is reported.
	AppliedRevision *int64
	// AppliedStartID identifies the boot that applied the policy.
	AppliedStartID string
	// Status is the open server apply-state vocabulary, such as pending or applied.
	Status string
	// Error is the server's apply failure explanation.
	Error string
}

// IfMatchOptions requires an exact ETag from the last read. Nil options or an
// empty IfMatch return ErrBadRequest; no unconditional update is performed.
type IfMatchOptions struct {
	// IfMatch is printable ASCII without leading or trailing whitespace.
	IfMatch string
}

type networkPolicyWire struct {
	BlockAll     *bool     `json:"block_all,omitempty"`
	AllowCIDRs   *[]string `json:"allow_cidrs,omitempty"`
	AllowDomains *[]string `json:"allow_domains,omitempty"`
}

func networkPolicyBody(spec NetworkPolicySpec) (networkPolicyWire, error) {
	domains := spec.AllowDomains != nil && *spec.AllowDomains != nil
	if domains && spec.BlockAll != nil && !*spec.BlockAll {
		return networkPolicyWire{}, newSDKError(CodeNetworkPolicyDomainsRequireBlockAll, 400,
			"network policy allowDomains requires blockAll to be true or omitted")
	}
	if len(spec.AllowCIDRs) > 0 && (spec.BlockAll == nil || !*spec.BlockAll) && !domains {
		return networkPolicyWire{}, badRequest(
			"network policy allowCIDRs restricts egress only with blockAll=true or allowDomains")
	}
	body := networkPolicyWire{BlockAll: clonePointer(spec.BlockAll)}
	if spec.AllowCIDRs != nil {
		cidrs := slices.Clone(spec.AllowCIDRs)
		body.AllowCIDRs = &cidrs
	}
	if spec.AllowDomains != nil {
		domains := slices.Clone(*spec.AllowDomains)
		body.AllowDomains = &domains
	}
	return body, nil
}

func sandboxIfMatch(opts *IfMatchOptions) error {
	if opts == nil {
		return badRequest("IfMatch must contain the ETag of the last read")
	}
	return headerText(opts.IfMatch, "IfMatch")
}
