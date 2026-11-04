// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/getkin/kin-openapi/openapi3"
	"github.com/getkin/kin-openapi/openapi3filter"
	"github.com/getkin/kin-openapi/routers"
	"github.com/getkin/kin-openapi/routers/gorillamux"
)

var (
	contractOnce          sync.Once
	apiContractRouter     routers.Router
	toolboxContractRouter routers.Router
	contractErr           error
)

func loadContracts(t *testing.T) (routers.Router, routers.Router) {
	t.Helper()
	contractOnce.Do(func() {
		apiContractRouter, contractErr = loadContractRouter(filepath.Join("..", "..", "contract", "api-openapi.json"))
		if contractErr != nil {
			return
		}
		toolboxContractRouter, contractErr = loadContractRouter(filepath.Join("..", "..", "contract", "toolbox-openapi.json"))
	})
	if contractErr != nil {
		t.Fatalf("load contract: %v", contractErr)
	}
	return apiContractRouter, toolboxContractRouter
}

func loadContractRouter(path string) (routers.Router, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var generic any
	if err := json.Unmarshal(raw, &generic); err != nil {
		return nil, err
	}
	downconvert31(generic)
	if m, ok := generic.(map[string]any); ok {
		m["openapi"] = "3.0.3"
	}
	raw, _ = json.Marshal(generic)
	loader := openapi3.NewLoader()
	doc, err := loader.LoadFromData(raw)
	if err != nil {
		return nil, err
	}
	doc.Servers = nil
	// Per-path `servers` too (neither published contract carries one today,
	// but a spec may pin a route to another listener): kin-openapi's
	// gorillamux router reuses the last per-path server list for every LATER
	// path, which would silently host-pin — and un-match — the whole rest of
	// the document.
	for _, item := range doc.Paths.Map() {
		item.Servers = nil
	}
	if err := doc.Validate(context.Background(), openapi3.DisableExamplesValidation(), openapi3.DisableSchemaDefaultsValidation()); err != nil {
		return nil, err
	}
	return gorillamux.NewRouter(doc)
}

func contractViolations(t *testing.T, method, path string, status int, body []byte, contentType string) []string {
	t.Helper()
	req, _ := http.NewRequest(method, "http://api"+path, nil)
	return responseContractViolations(t, req, status, body, contentType)
}

func validateAgainstContract(t *testing.T, method, path string, status int, body []byte, contentType string) {
	t.Helper()
	for _, violation := range contractViolations(t, method, path, status, body, contentType) {
		t.Errorf("fixture for %s %s does not match the contract:\n%v\nbody: %s", method, path, violation, truncate(body, 600))
	}
}

func validateAgainstContractRequest(t *testing.T, req *http.Request, status int, body []byte, contentType string) {
	t.Helper()
	for _, violation := range responseContractViolations(t, req, status, body, contentType) {
		t.Errorf("fixture for %s %s does not match the contract:\n%v\nbody: %s", req.Method, req.URL.Path, violation, truncate(body, 600))
	}
}

func responseContractViolations(t *testing.T, req *http.Request, status int, body []byte, contentType string) []string {
	t.Helper()
	target, violations := prepareContractTarget(t, req, nil)
	if len(violations) != 0 {
		return violations
	}
	if target == nil || status >= 400 || len(body) == 0 || !strings.HasPrefix(contentType, "application/json") {
		return nil
	}
	input := &openapi3filter.ResponseValidationInput{
		RequestValidationInput: &openapi3filter.RequestValidationInput{Request: target.request, PathParams: target.pathParams, Route: target.route, Options: &openapi3filter.Options{AuthenticationFunc: openapi3filter.NoopAuthenticationFunc}},
		Status:                 status,
		Header:                 http.Header{"Content-Type": []string{"application/json"}},
		Options:                &openapi3filter.Options{IncludeResponseStatus: true},
	}
	input.SetBodyBytes(body)
	if err := openapi3filter.ValidateResponse(context.Background(), input); err != nil {
		return []string{err.Error()}
	}
	return nil
}

func validateRequestAgainstContract(t *testing.T, req *http.Request, body []byte) {
	t.Helper()
	target, violations := prepareContractTarget(t, req, body)
	for _, violation := range violations {
		t.Errorf("request for %s %s is outside the contract:\n%v", req.Method, req.URL.Path, violation)
	}
	if target == nil {
		return
	}
	contentType := req.Header.Get("Content-Type")
	if len(body) != 0 && (strings.HasPrefix(contentType, "multipart/form-data") || strings.HasPrefix(contentType, "application/octet-stream")) {
		return // Streaming/multipart request bodies are fixture-heavy and validated separately by per-command assertions.
	}
	input := &openapi3filter.RequestValidationInput{
		Request:    target.request,
		PathParams: target.pathParams,
		Route:      target.route,
		Options:    &openapi3filter.Options{AuthenticationFunc: openapi3filter.NoopAuthenticationFunc},
	}
	if err := openapi3filter.ValidateRequest(context.Background(), input); err != nil {
		t.Errorf("request for %s %s does not match the contract:\n%v\nbody: %s", req.Method, req.URL.Path, err, truncate(body, 600))
	}
}

func downconvert31(v any) {
	switch node := v.(type) {
	case map[string]any:
		// Open wire enums accept future values, as declared by the contract.
		if node["x-daytona-open-enum"] == true {
			delete(node, "enum")
		}
		// 3.1-only keywords kin-openapi rejects as siblings of $ref/type.
		delete(node, "propertyNames")
		if types, ok := node["type"].([]any); ok {
			var rest []any
			nullable := false
			for _, t := range types {
				if t == "null" {
					nullable = true
				} else {
					rest = append(rest, t)
				}
			}
			if len(rest) == 1 {
				node["type"] = rest[0]
				if nullable {
					node["nullable"] = true
				}
			}
		}
		// `oneOf: [{type: null}, X]` → X + nullable.
		if branches, ok := node["oneOf"].([]any); ok {
			var kept []any
			nullable := false
			for _, b := range branches {
				if bm, ok := b.(map[string]any); ok && bm["type"] == "null" {
					nullable = true
					continue
				}
				kept = append(kept, b)
			}
			if nullable && len(kept) == 1 {
				delete(node, "oneOf")
				if km, ok := kept[0].(map[string]any); ok {
					if ref, isRef := km["$ref"]; isRef {
						// A nullable $ref cannot be expressed inline in 3.0
						// without allOf; use it so the target schema still applies.
						node["allOf"] = []any{map[string]any{"$ref": ref}}
					} else {
						for k, v := range km {
							node[k] = v
						}
					}
				}
				node["nullable"] = true
			} else if nullable {
				node["oneOf"] = kept
				node["nullable"] = true
			}
		}
		for _, child := range node {
			downconvert31(child)
		}
	case []any:
		for _, child := range node {
			downconvert31(child)
		}
	}
}

func TestContractLoads(t *testing.T) {
	r, toolbox := loadContracts(t)
	req, _ := http.NewRequest("GET", "http://api/orgs", nil)
	if _, _, err := r.FindRoute(req); err != nil {
		t.Fatal(err)
	}
	toolReq, _ := http.NewRequest("GET", "http://fixture/test-tools/sbx/port", nil)
	target, violations := prepareContractTarget(t, toolReq, nil)
	if len(violations) != 0 || target == nil {
		t.Fatalf("toolbox contract route missing: target=%v violations=%v", target, violations)
	}
	if _, _, err := toolbox.FindRoute(target.request); err != nil {
		t.Fatal(err)
	}
	var j map[string]any
	if err := json.Unmarshal([]byte(`{"organizations":[]}`), &j); err != nil {
		t.Fatal(err)
	}
}

func TestContractViolations_reportsWrongOrgListShape(t *testing.T) {
	violations := contractViolations(t, http.MethodGet, "/orgs", http.StatusOK, []byte(`[]`), "application/json")
	if len(violations) == 0 {
		t.Fatal("expected contract violation")
	}
	if !strings.Contains(violations[0], "OrganizationPage") || !strings.Contains(violations[0], "value must be an object") {
		t.Fatalf("unexpected violation: %v", violations)
	}
}

func TestContractViolations_reportsMissingSearchTotal(t *testing.T) {
	violations := contractViolations(t, http.MethodGet, "/sandboxes/search", http.StatusOK, []byte(`{"sandboxes":[],"total_relation":"eq"}`), "application/json")
	if len(violations) == 0 {
		t.Fatal("expected contract violation")
	}
	if !strings.Contains(violations[0], "SandboxSearchPage") || !strings.Contains(violations[0], `property "total" is missing`) {
		t.Fatalf("unexpected violation: %v", violations)
	}
}
