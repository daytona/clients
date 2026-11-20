// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "math"

func parseNetworkPolicyInfo(raw JSONValue, etag string) (*NetworkPolicyInfo, error) {
	object, err := parseObject(raw, "network policy")
	if err != nil {
		return nil, err
	}
	if etag == "" {
		return nil, badRequest("network policy response is missing the ETag header")
	}
	result := &NetworkPolicyInfo{ETag: etag}
	for name, target := range map[string]*string{
		"applied_start_id": &result.AppliedStartID, "status": &result.Status, "error": &result.Error,
	} {
		*target, err = field[string](object, name, false)
		if err != nil {
			return nil, err
		}
	}
	result.DesiredRevision, err = integerField(object, "desired_revision", true)
	if err != nil {
		return nil, err
	}
	result.AppliedRevision, err = sandboxOptionalInteger(object, "applied_revision")
	if err != nil {
		return nil, err
	}
	for name, target := range map[string]**NetworkPolicy{"desired": &result.Desired, "applied": &result.Applied} {
		if !present(object[name]) {
			continue
		}
		policy, parseErr := parseNetworkPolicy(object[name], name)
		if parseErr != nil {
			return nil, parseErr
		}
		*target = policy
	}
	return result, nil
}

func parseNetworkPolicy(raw JSONValue, name string) (*NetworkPolicy, error) {
	object, err := parseObject(raw, name)
	if err != nil {
		return nil, err
	}
	policy := &NetworkPolicy{}
	policy.Version, err = integerField(object, "version", true)
	if err != nil {
		return nil, err
	}
	if policy.Version < math.MinInt32 || policy.Version > math.MaxInt32 {
		return nil, badRequest("response field " + name + ".version must be an int32")
	}
	policy.BlockAll, err = field[bool](object, "block_all", true)
	if err != nil {
		return nil, err
	}
	policy.AllowCIDRs, err = sandboxStringList(object, "allow_cidrs", true)
	if err != nil {
		return nil, err
	}
	policy.AllowDomains, err = sandboxStringList(object, "allow_domains", false)
	if err != nil {
		return nil, err
	}
	return policy, nil
}

// Parse elements individually: encoding/json otherwise accepts null as "".
func sandboxStringList(object jsonObject, name string, required bool) ([]string, error) {
	rows, err := field[[]JSONValue](object, name, required)
	if err != nil {
		return nil, err
	}
	if rows == nil {
		return nil, nil
	}
	values := make([]string, len(rows))
	for index, raw := range rows {
		value, parseErr := field[string](jsonObject{name: raw}, name, true)
		if parseErr != nil {
			return nil, parseErr
		}
		values[index] = value
	}
	return values, nil
}

func sandboxOptionalInteger(object jsonObject, name string) (*int64, error) {
	if !present(object[name]) {
		return nil, nil //nolint:nilnil // An absent optional integer is represented by nil.
	}
	value, err := integerField(object, name, true)
	if err != nil {
		return nil, err
	}
	return &value, nil
}
