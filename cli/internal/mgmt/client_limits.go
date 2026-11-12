// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package mgmt

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/url"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
)

// OrgLimits preserves the response in Wire for JSON/YAML output. HasStorage
// tells the table to omit storage when the response does not report it,
// rather than print an unreported zero.
type OrgLimits struct {
	gen.OrganizationLimits
	Wire       map[string]any
	HasStorage bool
}

func GetOrgLimits(ctx context.Context, c *api.Client, orgID string) (*OrgLimits, error) {
	var raw json.RawMessage
	if err := c.Do(ctx, http.MethodGet, "/orgs/"+url.PathEscape(orgID)+"/limits", nil, nil, &raw); err != nil {
		return nil, err
	}
	out := &OrgLimits{}
	if err := json.Unmarshal(raw, &out.OrganizationLimits); err != nil {
		return nil, err
	}
	// UseNumber keeps int64 limits exact; float64 would round past 2^53.
	wire := json.NewDecoder(bytes.NewReader(raw))
	wire.UseNumber()
	if err := wire.Decode(&out.Wire); err != nil {
		return nil, err
	}
	var present struct {
		Storage json.RawMessage `json:"storage"`
	}
	if err := json.Unmarshal(raw, &present); err != nil {
		return nil, err
	}
	out.HasStorage = len(present.Storage) > 0 && string(present.Storage) != "null"
	return out, nil
}

// CurrentProjectKey describes the project API key the client presents,
// including the organization it acts in.
func CurrentProjectKey(ctx context.Context, c *api.Client) (*gen.ProjectApiKeyWithContext, error) {
	var resp gen.ProjectApiKeyWithContext
	if err := c.Do(ctx, http.MethodGet, "/api-keys/current", nil, nil, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}
