// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

const macPurchaseCreated = `{"toolbox_proxy_url":"https://proxy.example/test-tools","id":"33333333-3333-3333-3333-333333333333","name":"macbox","status":"pending","wait_cap_seconds":3,"reservation_id":null}`

func macPurchaseCommands() [][]string {
	return [][]string{
		{"sandbox", "create-macos", "--image", "base-macos", "--no-wait"},
		{"sandbox", "create-macos", "--image", "base-macos", "--reservation", "new", "--no-wait"},
		{"sandbox", "create-macos", "--image", "base-macos", "--reservation", "", "--no-wait"},
		{"reservation", "create", "--region", "region-a"},
		{"reservation", "create", "--region", "region-a", "--count", "2"},
	}
}

func macPurchaseAPI(t *testing.T) *fakeAPI {
	t.Helper()
	f := newFakeAPI(t)
	f.on(http.MethodPost, "/sandboxes/macos", 202, macPurchaseCreated)
	f.on(http.MethodPost, "/reservations", 201, macReservationFixture(`"m4-10c-16g"`, m4Capacity))
	f.on(http.MethodPost, "/reservations/bulk", 200, `{"requested":2,"reserved":2,"reservations":[`+macReservationFixture(`"m4-10c-16g"`, m4Capacity)+`,`+macReservationFixture(`"m4-10c-16g"`, m4Capacity)+`]}`)
	return f
}

func TestMacPurchase_refuses_when_offTTYWithoutConsent(t *testing.T) {
	for _, args := range macPurchaseCommands() {
		t.Run(args[0]+args[len(args)-1], func(t *testing.T) {
			// Given
			f := macPurchaseAPI(t)
			t.Setenv("DAYTONA_YES", "")
			env := map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}
			// When
			_, _, code := run(t, cfgDir(t), env, args...)
			// Then
			require.Equal(t, cerr.ExitUsage, code)
			require.Empty(t, f.requests)
		})
	}
}

// `config set confirm false` turns prompts off at a terminal only; off a
// terminal a purchase still needs --yes or DAYTONA_YES=1.
func TestMacPurchase_refuses_when_onlyConfigConfirmIsOff(t *testing.T) {
	for _, args := range macPurchaseCommands() {
		t.Run(args[0]+args[len(args)-1], func(t *testing.T) {
			f := macPurchaseAPI(t)
			dir := cfgDir(t)
			t.Setenv("DAYTONA_YES", "")
			_, _, code := run(t, dir, nil, "config", "set", "confirm", "false")
			require.Zero(t, code)
			_, _, code = run(t, dir, map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}, args...)
			require.Equal(t, cerr.ExitUsage, code)
			require.Empty(t, f.requests)
		})
	}
}

func TestMacPurchase_posts_when_consentIsExplicit(t *testing.T) {
	for _, consent := range []string{"flag", "environment"} {
		for _, args := range macPurchaseCommands() {
			t.Run(consent+args[0]+args[len(args)-1], func(t *testing.T) {
				// Given
				f := macPurchaseAPI(t)
				dir := cfgDir(t)
				t.Setenv("DAYTONA_YES", "")
				env := map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}
				switch consent {
				case "flag":
					args = append(append([]string{}, args...), "--yes")
				case "environment":
					env["DAYTONA_YES"] = "1"
				}
				// When
				_, stderr, code := run(t, dir, env, args...)
				// Then
				require.Zero(t, code, stderr)
				require.Len(t, f.requests, 1)
				require.Equal(t, http.MethodPost, f.requests[0].Method)
			})
		}
	}
}

func TestMacPurchase_postsWithoutConsent_when_existingReservation(t *testing.T) {
	for _, source := range []string{"flag", "file", "flag-over-file"} {
		t.Run(source, func(t *testing.T) {
			// Given
			f := macPurchaseAPI(t)
			t.Setenv("DAYTONA_YES", "")
			args := []string{"sandbox", "create-macos", "--image", "base-macos", "--no-wait"}
			if source != "flag" {
				body := `{"reservation":"region-a:res-1"}`
				if source == "flag-over-file" {
					body = `{"reservation":"new"}`
				}
				file := filepath.Join(t.TempDir(), "body.json")
				require.NoError(t, os.WriteFile(file, []byte(body), 0o600))
				args = append(args, "--file", file)
			}
			if source != "file" {
				args = append(args, "--reservation", "region-a:res-1")
			}
			// When
			_, stderr, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"}, args...)
			// Then
			require.Zero(t, code, stderr)
			require.Len(t, f.requests, 1)
			require.Equal(t, "region-a:res-1", f.requests[0].Body["reservation"])
		})
	}
}

func TestMacPurchase_dryRunReadsOnly_when_withoutConsent(t *testing.T) {
	// Given
	t.Setenv("DAYTONA_YES", "")
	requests := []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()},
		{method: http.MethodGet, path: "/mac-catalog", query: "region_ids=region-b", status: 200, response: macCatalogFixture()},
	}
	// When
	_, _, err := runCLI(t, []string{"reservation", "create", "--region", "region-b", "--dry-run"}, requests)
	// Then
	require.NoError(t, err)
}

func TestMacPurchase_refuses_when_fileMayBuy(t *testing.T) {
	for _, reservation := range []string{`"new"`, `null`, `""`} {
		t.Run(reservation, func(t *testing.T) {
			// Given
			f := macPurchaseAPI(t)
			t.Setenv("DAYTONA_YES", "")
			file := filepath.Join(t.TempDir(), "body.json")
			require.NoError(t, os.WriteFile(file, []byte(`{"image":"base-macos","reservation":`+reservation+`}`), 0o600))
			// When
			_, _, code := run(t, cfgDir(t), map[string]string{"DAYTONA_V2_API_URL": f.srv.URL, "DAYTONA_API_KEY": "k"},
				"sandbox", "create-macos", "--file", file, "--no-wait")
			// Then
			require.Equal(t, cerr.ExitUsage, code)
			require.Empty(t, f.requests)
		})
	}
}
