// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package api

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestSpecialCodes_areInContractCatalogs(t *testing.T) {
	t.Parallel()

	apiCodes := loadOpenAPIEnum(t, "api-openapi.json", "ApiErrorCode")
	requireCatalogContains(t, "api", apiCodes, SpecialAPICodes)

	daemonCodes := loadOpenAPIEnum(t, "toolbox-openapi.json", "ErrorCode")
	requireCatalogContains(t, "daemon", daemonCodes, SpecialDaemonCodes)

	proxyCodes := loadProxyCodes(t, "proxy-errors.json")
	requireCatalogContains(t, "proxy", proxyCodes, SpecialProxyCodes)

	routerCodes := loadProxyCodes(t, "router-errors.json")
	requireCatalogContains(t, "router", routerCodes, SpecialRouterCodes)
}

func loadOpenAPIEnum(t *testing.T, fileName, schema string) map[string]struct{} {
	t.Helper()

	var spec struct {
		Components struct {
			Schemas map[string]struct {
				Enum []string `json:"enum"`
			} `json:"schemas"`
		} `json:"components"`
	}
	readContractJSON(t, fileName, &spec)

	entry, ok := spec.Components.Schemas[schema]
	if !ok {
		t.Fatalf("schema %q missing from %s", schema, fileName)
	}
	return stringSet(entry.Enum)
}

func loadProxyCodes(t *testing.T, fileName string) map[string]struct{} {
	t.Helper()

	var spec struct {
		Codes []struct {
			Code string `json:"code"`
		} `json:"codes"`
	}
	readContractJSON(t, fileName, &spec)

	values := make([]string, 0, len(spec.Codes))
	for _, code := range spec.Codes {
		values = append(values, code.Code)
	}
	return stringSet(values)
}

func readContractJSON(t *testing.T, fileName string, out any) {
	t.Helper()

	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	path := filepath.Join(filepath.Dir(file), "..", "..", "..", "contract", fileName)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, out); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func requireCatalogContains(t *testing.T, catalog string, known map[string]struct{}, special []string) {
	t.Helper()

	for _, code := range special {
		if _, ok := known[code]; !ok {
			t.Fatalf("%s special-case code %q missing from contract catalog", catalog, code)
		}
	}
}

func stringSet(values []string) map[string]struct{} {
	set := make(map[string]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	return set
}
