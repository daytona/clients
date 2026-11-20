// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

// Package e2e_test holds the live tests of the Go SDK against a Daytona
// deployment. They need DAYTONA_API_KEY (and DAYTONA_V2_API_URL for a
// deployment other than Daytona Cloud) and run with the build tag e2e:
//
//	DAYTONA_API_KEY=... go test -tags e2e -count=1 ./e2e/...
//
// Every test labels what it creates with purpose=gosdk-e2e and deletes it in
// t.Cleanup.
package e2e_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

// Label every resource a test creates carries.
const (
	labelKey   = "purpose"
	labelValue = "gosdk-e2e"
)

// newClient returns a client built from the environment, closed when the
// test ends. The test is skipped when DAYTONA_API_KEY is not set.
func newClient(t *testing.T) *daytona.Client {
	t.Helper()
	if os.Getenv("DAYTONA_API_KEY") == "" {
		t.Skip("DAYTONA_API_KEY is not set")
	}
	client, err := daytona.New()
	if err != nil {
		t.Fatalf("daytona.New: %v", err)
	}
	t.Cleanup(func() {
		if closeErr := client.Close(); closeErr != nil {
			t.Errorf("Close: %v", closeErr)
		}
	})
	return client
}

// testContext returns a context bounded to 5 minutes, cancelled when the test
// ends.
func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	t.Cleanup(cancel)
	return ctx
}

// uniqueName returns prefix plus a random suffix, valid as a sandbox, image
// or volume name.
func uniqueName(t *testing.T, prefix string) string {
	t.Helper()
	suffix := make([]byte, 4)
	if _, err := rand.Read(suffix); err != nil {
		t.Fatalf("random name: %v", err)
	}
	return prefix + "-" + hex.EncodeToString(suffix)
}

// rawSandbox creates a running sandbox with plain HTTP, independent of the
// SDK's own create, and deletes it when the test ends. Like an SDK create, it
// stamps the name label that Client.FindByName looks up. It returns the ID.
func rawSandbox(t *testing.T, name string) string {
	t.Helper()
	ctx := testContext(t)
	body, err := json.Marshal(map[string]any{
		"name":         name,
		"labels":       map[string]string{labelKey: labelValue, "name": name},
		"wait_seconds": 60,
	})
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var created struct {
		ID string `json:"id"`
	}
	if createErr := rawAPI(ctx, http.MethodPost, "/sandboxes", body, &created); createErr != nil {
		t.Fatalf("create sandbox %s: %v", name, createErr)
	}
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		if deleteErr := rawAPI(cleanupCtx, http.MethodDelete, "/sandboxes/"+created.ID, nil, nil); deleteErr != nil {
			t.Errorf("delete sandbox %s: %v", created.ID, deleteErr)
		}
	})
	// The server's wait_seconds hold is best effort: poll until it runs.
	for {
		var current struct {
			Status string `json:"status"`
		}
		if getErr := rawAPI(ctx, http.MethodGet, "/sandboxes/"+created.ID, nil, &current); getErr != nil {
			t.Fatalf("read sandbox %s: %v", created.ID, getErr)
		}
		if current.Status == "running" {
			return created.ID
		}
		select {
		case <-ctx.Done():
			t.Fatalf("sandbox %s did not reach running (last status %q): %v", created.ID, current.Status, ctx.Err())
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// rawAPI sends one API request with plain HTTP. Idempotent requests (GET,
// DELETE) are retried a few times on a gateway error (502, 503, 504), which
// the live deployment answers now and then; everything else is returned.
func rawAPI(ctx context.Context, method, path string, body []byte, out any) error {
	attempts := 1
	if method == http.MethodGet || method == http.MethodDelete {
		attempts = 3
	}
	var err error
	for attempt := range attempts {
		var status int
		status, err = rawAPIOnce(ctx, method, path, body, out)
		if err == nil || (status != http.StatusBadGateway && status != http.StatusServiceUnavailable &&
			status != http.StatusGatewayTimeout) {
			return err
		}
		select {
		case <-ctx.Done():
			return err
		case <-time.After(time.Duration(attempt+1) * time.Second):
		}
	}
	return err
}

func rawAPIOnce(ctx context.Context, method, path string, body []byte, out any) (int, error) {
	base := strings.TrimRight(os.Getenv("DAYTONA_V2_API_URL"), "/")
	if base == "" {
		base = daytona.DefaultAPIURL
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Authorization", "Bearer "+os.Getenv("DAYTONA_API_KEY"))
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0, err
	}
	data, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	closeErr := resp.Body.Close()
	if readErr != nil {
		return resp.StatusCode, readErr
	}
	if resp.StatusCode >= 300 {
		return resp.StatusCode, fmt.Errorf("%s %s: HTTP %d: %s", method, path, resp.StatusCode, data)
	}
	if closeErr != nil {
		return resp.StatusCode, closeErr
	}
	if out == nil {
		return resp.StatusCode, nil
	}
	return resp.StatusCode, json.Unmarshal(data, out)
}

// eventually retries check every half second until it reports true or the
// bound passes. Sandbox search is backed by an index that trails writes by a
// few seconds.
func eventually(ctx context.Context, t *testing.T, bound time.Duration, check func() (bool, error)) {
	t.Helper()
	ctx, cancel := context.WithTimeout(ctx, bound)
	defer cancel()
	for {
		done, err := check()
		if err != nil {
			t.Fatal(err)
		}
		if done {
			return
		}
		select {
		case <-ctx.Done():
			t.Fatalf("condition not met within %s", bound)
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// skipIfServerLacks skips the test when err is a server answer that means the
// deployment or the API key does not support the operation yet (a missing
// permission or feature), naming it, so the suite reports what it could not
// check instead of failing on account setup. Every other error is returned.
func skipIfServerLacks(t *testing.T, err error, what string) error {
	t.Helper()
	var apiErr *daytona.Error
	if errors.As(err, &apiErr) && (apiErr.Code == daytona.CodeFeatureNotEnabled ||
		(apiErr.StatusCode == http.StatusForbidden && strings.Contains(apiErr.Message, "permission"))) {
		t.Skipf("%s: not available to this key on this deployment: %v", what, err)
	}
	return err
}
