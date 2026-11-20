// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"time"
)

// Secret contains caller-owned secret metadata, never the write-only value.
// Unknown server fields (including any value) are discarded at the boundary.
type Secret struct {
	// Name is the project-unique secret name.
	Name string
	// AllowedDomains lists permitted HTTPS destination hosts.
	AllowedDomains []string
	// Description is nil when absent; a pointer to "" is an explicit empty description.
	Description *string
	// CreatedAt is the creation timestamp.
	CreatedAt time.Time
	// UpdatedAt is the metadata update timestamp.
	UpdatedAt time.Time
}

func parseSecret(raw JSONValue) (Secret, error) {
	var result Secret
	object, err := parseObject(raw, "secret")
	if err != nil {
		return result, err
	}
	result.Name, err = field[string](object, "name", true)
	if err != nil {
		return result, err
	}
	domains, err := field[[]JSONValue](object, "allowed_domains", true)
	if err != nil {
		return result, err
	}
	result.AllowedDomains = make([]string, 0, len(domains))
	for _, rawDomain := range domains {
		var domain string
		if !present(rawDomain) || json.Unmarshal(rawDomain, &domain) != nil {
			return result, badRequest("response field allowed_domains must contain strings")
		}
		result.AllowedDomains = append(result.AllowedDomains, domain)
	}
	if present(object["description"]) {
		description, parseErr := field[string](object, "description", true)
		if parseErr != nil {
			return result, parseErr
		}
		result.Description = &description
	}
	for _, entry := range []struct {
		name   string
		target *time.Time
	}{{"created_at", &result.CreatedAt}, {"updated_at", &result.UpdatedAt}} {
		if !present(object[entry.name]) {
			return result, badRequest("response field " + entry.name + " is required")
		}
		stamp, parseErr := timestampField(object, entry.name)
		if parseErr != nil {
			return result, parseErr
		}
		*entry.target = stamp
	}
	return result, nil
}
