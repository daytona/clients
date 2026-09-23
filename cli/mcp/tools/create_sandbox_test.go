// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package tools

import (
	"strings"
	"testing"
)

func TestCreateSandboxToolNetworkAllowListSchema(t *testing.T) {
	tool := GetCreateSandboxTool()
	networkDesc := schemaDescription(t, tool.InputSchema.Properties, "networkAllowList")
	domainDesc := schemaDescription(t, tool.InputSchema.Properties, "domainAllowList")
	kvmDesc := schemaDescription(t, tool.InputSchema.Properties, "kvm")

	for _, needle := range []string{"IPv4", "CIDR", "domainAllowList"} {
		if !strings.Contains(networkDesc, needle) {
			t.Errorf("networkAllowList description %q does not contain %q", networkDesc, needle)
		}
	}
	if strings.Contains(networkDesc, "list of domains") {
		t.Errorf("networkAllowList description still claims to accept domains: %q", networkDesc)
	}
	if !strings.Contains(networkDesc, "Cannot be combined with a non-empty domainAllowList") {
		t.Errorf("networkAllowList description should document two-way exclusivity with domainAllowList: %q", networkDesc)
	}
	if strings.Contains(strings.ToLower(networkDesc), "mutually exclusive") && strings.Contains(networkDesc, "networkBlockAll") {
		t.Errorf("networkAllowList description overclaims exclusivity with networkBlockAll: %q", networkDesc)
	}

	if !strings.Contains(domainDesc, "domains") {
		t.Errorf("domainAllowList description %q does not mention domains", domainDesc)
	}
	if !strings.Contains(domainDesc, "*.") && !strings.Contains(strings.ToLower(domainDesc), "wildcard") {
		t.Errorf("domainAllowList description %q does not mention wildcard domains", domainDesc)
	}
	if strings.Contains(strings.ToLower(domainDesc), "mutually exclusive") && strings.Contains(domainDesc, "networkBlockAll") {
		t.Errorf("domainAllowList description overclaims exclusivity with networkBlockAll: %q", domainDesc)
	}
	if kvmDesc != "Expose KVM (/dev/kvm) inside the sandbox via nested virtualization. linux-vm snapshots only. Requires the sandbox_kvm feature for the organization." {
		t.Errorf("kvm description = %q", kvmDesc)
	}

	for _, field := range []string{"networkAllowList", "domainAllowList"} {
		for _, required := range tool.InputSchema.Required {
			if required == field {
				t.Errorf("%s must be optional, but is required", field)
			}
		}
	}
}

func TestCreateSandboxRequestWiresNetworkSettings(t *testing.T) {
	cidr := "10.0.0.0/8"
	domains := "example.com,*.daytona.io"
	empty := ""
	whitespace := "   "
	blockAll := true
	kvm := true
	name := "allowlist-test"

	tests := []struct {
		name         string
		args         CreateSandboxArgs
		wantCIDR     *string
		wantDomains  *string
		wantBlockAll *bool
		wantKvm      bool
		wantName     *string
	}{
		{
			name: "unset",
			args: CreateSandboxArgs{},
		},
		{
			name:     "cidr only",
			args:     CreateSandboxArgs{NetworkAllowList: &cidr},
			wantCIDR: &cidr,
		},
		{
			name:        "domain only",
			args:        CreateSandboxArgs{DomainAllowList: &domains},
			wantDomains: &domains,
		},
		{
			name:        "cidr with empty domain list",
			args:        CreateSandboxArgs{NetworkAllowList: &cidr, DomainAllowList: &empty},
			wantCIDR:    &cidr,
			wantDomains: &empty,
		},
		{
			name:        "cidr with whitespace domain list",
			args:        CreateSandboxArgs{NetworkAllowList: &cidr, DomainAllowList: &whitespace},
			wantCIDR:    &cidr,
			wantDomains: &whitespace,
		},
		{
			name:         "block all",
			args:         CreateSandboxArgs{NetworkBlockAll: &blockAll},
			wantBlockAll: &blockAll,
		},
		{
			name:    "kvm only",
			args:    CreateSandboxArgs{Kvm: &kvm},
			wantKvm: true,
		},
		{
			name:     "name and cidr",
			args:     CreateSandboxArgs{Name: &name, NetworkAllowList: &cidr},
			wantCIDR: &cidr,
			wantName: &name,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req, err := createSandboxRequest(tt.args)
			if err != nil {
				t.Fatalf("createSandboxRequest() error = %v", err)
			}

			gotCIDR, cidrSet := req.GetNetworkAllowListOk()
			assertOptionalString(t, "networkAllowList", tt.wantCIDR, gotCIDR, cidrSet)
			gotDomains, domainsSet := req.GetDomainAllowListOk()
			assertOptionalString(t, "domainAllowList", tt.wantDomains, gotDomains, domainsSet)
			gotBlockAll, blockSet := req.GetNetworkBlockAllOk()
			if tt.wantBlockAll == nil {
				if blockSet {
					t.Errorf("networkBlockAll set unexpectedly to %v", *gotBlockAll)
				}
			} else if !blockSet || *gotBlockAll != *tt.wantBlockAll {
				t.Errorf("networkBlockAll = %v set=%v, want %v", gotBlockAll, blockSet, *tt.wantBlockAll)
			}
			if tt.wantName != nil && req.GetName() != *tt.wantName {
				t.Errorf("name = %q, want %q", req.GetName(), *tt.wantName)
			}
			if req.GetKvm() != tt.wantKvm {
				t.Errorf("kvm = %v, want %v", req.GetKvm(), tt.wantKvm)
			}
		})
	}
}

func TestCreateSandboxRequestRejectsBothAllowLists(t *testing.T) {
	cidr := "10.0.0.0/8"
	paddedCIDR := " 10.0.0.0/8 "
	domains := "example.com"
	paddedDomains := " example.com "

	tests := []struct {
		name string
		args CreateSandboxArgs
	}{
		{
			name: "non-empty cidr and domains",
			args: CreateSandboxArgs{NetworkAllowList: &cidr, DomainAllowList: &domains},
		},
		{
			name: "padded cidr and domains",
			args: CreateSandboxArgs{NetworkAllowList: &paddedCIDR, DomainAllowList: &paddedDomains},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := createSandboxRequest(tt.args)
			if err == nil {
				t.Fatal("expected createSandboxRequest() error, got nil")
			}
			if !strings.Contains(err.Error(), "networkAllowList and domainAllowList are mutually exclusive") {
				t.Fatalf("error %q does not name the conflicting fields", err)
			}
		})
	}
}

func schemaDescription(t *testing.T, properties map[string]any, field string) string {
	t.Helper()
	raw, ok := properties[field]
	if !ok {
		t.Fatalf("schema missing property %q", field)
	}
	prop, ok := raw.(map[string]any)
	if !ok {
		t.Fatalf("property %q is %T, want map[string]any", field, raw)
	}
	desc, _ := prop["description"].(string)
	if desc == "" {
		t.Fatalf("property %q has empty description", field)
	}
	return desc
}

func assertOptionalString(t *testing.T, field string, want *string, got *string, set bool) {
	t.Helper()
	if want == nil {
		if set {
			t.Errorf("%s set unexpectedly to %q", field, *got)
		}
		return
	}
	if !set || got == nil || *got != *want {
		t.Errorf("%s = %v set=%v, want %q", field, got, set, *want)
	}
}
