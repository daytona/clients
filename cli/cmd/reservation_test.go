// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/stretchr/testify/require"
)

func macReservationFixture(model, capacity string) string {
	return `{"id":"region-a:res-1","org_id":"11111111-1111-1111-1111-111111111111","project_id":"22222222-2222-2222-2222-222222222222",` +
		`"region_id":"region-a","status":"active","origin":"explicit","auto_renew":true,"slots_used":1,` +
		`"period_n":1,"period_seconds":86400,"renewal_pending":false,"created_at":"2026-10-01T00:00:00Z","period_end":"2026-10-02T00:00:00Z",` +
		`"model":` + model + `,"capacity":` + capacity + `,"resources_used":{"cores":5,"memory_mib":8192,"disk_mib":81920}}`
}

const (
	m4Capacity    = `{"cores":10,"memory_mib":16384,"disk_mib":163840}`
	m4proCapacity = `{"cores":12,"memory_mib":24576,"disk_mib":122880}`
	m4Model       = `{"id":"m4-10c-16g","name":"Apple M4","cores":10,"memory_mib":16384,"disk_mib":244198,"sandbox_disk_mib":163840,"max_sandboxes":2,"default_size":{"cores":5,"memory_mib":8192,"disk_mib":81920}}`
	tinyModel     = `{"id":"tiny-2c-4g","name":"Apple Tiny","cores":2,"memory_mib":4096,"disk_mib":65536,"sandbox_disk_mib":40960,"max_sandboxes":0,"default_size":null}`
	m4proModel    = `{"id":"m4pro-12c-24g","name":"Apple M4 Pro","cores":12,"memory_mib":24576,"disk_mib":488386,"sandbox_disk_mib":122880,"max_sandboxes":1,"default_size":{"cores":12,"memory_mib":24576,"disk_mib":122880}}`
)

// macCatalogFixture is `GET /mac-catalog` for region-b: m4-10c-16g priced
// with 4 free Macs, m4pro-12c-24g unpriced with none.
func macCatalogFixture() string {
	return `{"regions":[{"id":"region-b","default_model":"m4-10c-16g","models":[` +
		`{"model":` + m4Model + `,"available":4,"price":{"cents_per_hour":135.12,"period_cents":3243,"currency":"usd","period_seconds":86400}},` +
		`{"model":` + m4proModel + `,"available":0,"price":null}]}]}`
}

func objectRows(t *testing.T, stdout string) map[string]string {
	t.Helper()
	rows := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(stdout), "\n") {
		fields := strings.Fields(line)
		require.NotEmpty(t, fields, "blank row in %q", stdout)
		rows[fields[0]] = strings.Join(fields[1:], " ")
	}
	return rows
}

func squashSpaces(s string) string { return strings.Join(strings.Fields(s), " ") }

// stderrOf renders err the way main prints it.
func stderrOf(err error) string {
	var stderr bytes.Buffer
	printError(&stderr, err)
	return stderr.String()
}

func TestMacModelList_registryTable(t *testing.T) {
	page := `{"mac_models":[` + m4Model + `,` + m4proModel + `,` + tinyModel + `]}`
	stdout, _, err := runCLIWithOutput(t, "table", []string{"mac-model", "list"}, []expectedRequest{{method: http.MethodGet, path: "/mac-models", status: 200, response: page}})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 4)
	require.Equal(t, []string{"ID", "NAME", "CORES", "MEMORY_MIB", "DISK_MIB", "SANDBOX_DISK_MIB", "MAX_SANDBOXES", "DEFAULT_SIZE"}, strings.Fields(lines[0]))
	require.Equal(t, "m4-10c-16g Apple M4 10 16384 244198 163840 2 5 cores / 8192 MiB / 81920 MiB disk", squashSpaces(lines[1]))
	require.Equal(t, "m4pro-12c-24g Apple M4 Pro 12 24576 488386 122880 1 12 cores / 24576 MiB / 122880 MiB disk", squashSpaces(lines[2]))
	require.Equal(t, "tiny-2c-4g Apple Tiny 2 4096 65536 40960 0 -", squashSpaces(lines[3]))

	stdout, _, err = runCLI(t, []string{"mac-model", "list"}, []expectedRequest{{method: http.MethodGet, path: "/mac-models", status: 200, response: page}})
	require.NoError(t, err)
	require.JSONEq(t, `{"items":[`+m4Model+`,`+m4proModel+`,`+tinyModel+`],"incomplete":false}`, stdout)
}

// `--region` reads the regional API's GET /mac-catalog, reached as
// `cpu-type list --region` reaches GET /cpu-catalog, and appends AVAILABLE
// and the two prices.
func TestMacModelList_regionAddsAvailabilityAndPrices(t *testing.T) {
	routing := expectedRequest{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()}
	catalog := expectedRequest{method: http.MethodGet, path: "/mac-catalog", query: "region_ids=region-b", status: 200, response: macCatalogFixture()}

	stdout, _, err := runCLIWithOutput(t, "table", []string{"mac-model", "list", "--region", " Region-B"}, []expectedRequest{routing, catalog})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 3)
	require.Equal(t, []string{"ID", "NAME", "CORES", "MEMORY_MIB", "DISK_MIB", "SANDBOX_DISK_MIB", "MAX_SANDBOXES", "DEFAULT_SIZE", "AVAILABLE", "PRICE_PER_HOUR", "PRICE_PER_PERIOD"}, strings.Fields(lines[0]))
	require.True(t, strings.HasSuffix(squashSpaces(lines[1]), "81920 MiB disk 4 $1.3512 $32.43"), lines[1])
	require.True(t, strings.HasSuffix(squashSpaces(lines[2]), "122880 MiB disk 0 - -"), lines[2])

	stdout, _, err = runCLI(t, []string{"mac-model", "list", "--region", "region-b"}, []expectedRequest{routing, catalog})
	require.NoError(t, err)
	// The list envelope holds the region's catalog entries as the API sends
	// them (a null price stays null).
	var fixture struct {
		Regions []struct {
			Models []json.RawMessage `json:"models"`
		} `json:"regions"`
	}
	require.NoError(t, json.Unmarshal([]byte(macCatalogFixture()), &fixture))
	want, err := json.Marshal(map[string]any{"items": fixture.Regions[0].Models, "incomplete": false})
	require.NoError(t, err)
	require.JSONEq(t, string(want), stdout)
	require.Contains(t, stdout, `"price": null`)

	_, _, err = runCLIWithOutput(t, "table", []string{"mac-model", "list", "--region", "region-b,region-a"}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Equal(t, "error: --region takes one region slug\n", stderrOf(err))

	other := catalog
	other.response = strings.Replace(macCatalogFixture(), `"id":"region-b"`, `"id":"region-c"`, 1)
	_, _, err = runCLIWithOutput(t, "table", []string{"mac-model", "list", "--region", "region-b"}, []expectedRequest{routing, other})
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
	require.Equal(t, "error: GET /mac-catalog did not answer for region region-b (it covered region-c)\n", stderrOf(err))

	help, _, err := runCLI(t, []string{"mac-model", "list", "--help"}, nil)
	require.NoError(t, err)
	for _, line := range []string{
		"--region REGION region slug: also show free Macs and prices from GET /mac-catalog",
		"With --region, also show how many Macs of each model a purchase can reserve now (AVAILABLE) and the price of one Mac-hour and one 24-hour period.",
		"daytona mac-model list --region '<region>'",
	} {
		require.Contains(t, squashSpaces(help), line)
	}
}

func TestFormatCents(t *testing.T) {
	require.Equal(t, "$32.43", formatCents(3243))
	require.Equal(t, "$0.05", formatCents(5))
	require.Equal(t, "$97.29", formatCents(9729))
	require.Equal(t, "$1.3512", formatCentsPerHour(135.12))
	require.Equal(t, "$1.35", formatCentsPerHour(135))
	require.Equal(t, "$0.05", formatCentsPerHour(5))
	require.Equal(t, "$0.005", formatCentsPerHour(0.5))
	require.Equal(t, "$12.00", formatCentsPerHour(1200))
}

// --region wins over the --file body; DAYTONA_REGION is not read.
func TestReservationRegion_precedence(t *testing.T) {
	getenv := func(key string) string {
		if key == "DAYTONA_REGION" {
			return "env"
		}
		return ""
	}
	resolve := func(flag string, body input.Body) (string, error) {
		return reservationRegion(&app.App{Globals: app.Globals{Region: flag}, Getenv: getenv}, body)
	}
	region, err := resolve("flag", input.Body{"region_id": "file"})
	require.NoError(t, err)
	require.Equal(t, "flag", region)
	region, err = resolve("", input.Body{"region_id": "file"})
	require.NoError(t, err)
	require.Equal(t, "file", region)
	_, err = resolve("", input.Body{})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestReservationGetAndList_printMissingCapacityWithDashes(t *testing.T) {
	missingCapacity := macReservationFixture(`null`, `null`)
	stdout, _, err := runCLIWithOutput(t, "table", []string{"reservation", "get", "region-a:res-1"}, []expectedRequest{{
		method: http.MethodGet, path: "/reservations/region-a:res-1", status: 200, response: missingCapacity,
	}})
	require.NoError(t, err)
	rows := objectRows(t, stdout)
	require.Equal(t, "-", rows["model"])
	require.Equal(t, "-", rows["cores_total"])
	require.Equal(t, "-", rows["memory_mib_total"])
	require.Equal(t, "-", rows["disk_mib_total"])
	require.Equal(t, "81920", rows["disk_mib_used"])
	require.Equal(t, "5", rows["cores_used"])
	_, listed := rows["sandboxes_per_mac"]
	require.False(t, listed, "the API no longer reports sandboxes_per_mac")

	list := `{"reservations":[` + macReservationFixture(`"m4-10c-16g"`, m4Capacity) + `,` + missingCapacity + `]}`
	stdout, _, err = runCLIWithOutput(t, "table", []string{"reservation", "list"}, []expectedRequest{{
		method: http.MethodGet, path: "/reservations", query: "limit=100", status: 200, response: list,
	}})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 3)
	require.Equal(t, []string{"ID", "REGION", "MODEL", "STATUS", "CORES_USED", "CORES_TOTAL", "MEMORY_MIB_USED", "MEMORY_MIB_TOTAL", "DISK_MIB_USED", "DISK_MIB_TOTAL", "AUTO_RENEW", "PERIOD_END"}, strings.Fields(lines[0]))
	require.Equal(t, []string{"region-a:res-1", "region-a", "m4-10c-16g", "active", "5", "10", "8192", "16384", "81920", "163840", "true", "2026-10-02T00:00:00Z"}, strings.Fields(lines[1]))
	require.Equal(t, []string{"region-a:res-1", "region-a", "-", "active", "5", "-", "8192", "-", "81920", "-", "true", "2026-10-02T00:00:00Z"}, strings.Fields(lines[2]))
}

// renew sends the caller's key, or a fresh one per invocation, and prints the
// `reservation get` fields.
func TestReservationRenew_sendsAKeyPerInvocation(t *testing.T) {
	renewed := macReservationFixture(`"m4-10c-16g"`, m4Capacity)
	_, _, err := runCLI(t, []string{"reservation", "renew", "region-a:res-1"}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "renewing charges the organization: off a terminal it needs --yes")
	require.ErrorContains(t, err, "--yes")

	stdout, _, err := runCLIWithOutput(t, "table", []string{"reservation", "renew", "region-a:res-1", "--idempotency-key", "renew-region-a:res-1-2", "--yes"}, []expectedRequest{{
		method: http.MethodPost, path: "/reservations/region-a:res-1/renew", status: 200, response: renewed,
		check: func(t *testing.T, r *http.Request, body []byte) {
			require.Equal(t, "renew-region-a:res-1-2", r.Header.Get("Idempotency-Key"))
			require.Empty(t, body)
		},
	}})
	require.NoError(t, err)
	rows := objectRows(t, stdout)
	require.Equal(t, "2026-10-02T00:00:00Z", rows["period_end"])

	var keys []string
	fresh := expectedRequest{
		method: http.MethodPost, path: "/reservations/region-a:res-1/renew", status: 200, response: renewed,
		check: func(t *testing.T, r *http.Request, _ []byte) { keys = append(keys, r.Header.Get("Idempotency-Key")) },
	}
	for range 2 {
		_, _, err = runCLI(t, []string{"reservation", "renew", "region-a:res-1", "--yes"}, []expectedRequest{fresh})
		require.NoError(t, err)
	}
	require.Len(t, keys, 2)
	require.NotEmpty(t, keys[0])
	require.NotEqual(t, keys[0], keys[1])

	help, _, err := runCLI(t, []string{"reservation", "renew", "--help"}, nil)
	require.NoError(t, err)
	text := squashSpaces(help)
	for _, line := range []string{
		"Charge the next period of an active reservation (POST /reservations/{id}/renew); period_end moves out by one period on the same Mac. The SDK twin is reservations.renew(…).",
		"Without --idempotency-key the CLI sends a fresh key; pass your own to retry without paying for a second period.",
		"Asks for confirmation at a terminal; off a terminal pass --yes (or set DAYTONA_YES=1).",
		"daytona reservation renew RESERVATION_ID --idempotency-key renew-RESERVATION_ID-2 --yes",
		"--idempotency-key KEY a retry with the same key returns the reservation without charging another period (default: a fresh key per invocation)",
	} {
		require.Contains(t, text, line)
	}
}

func TestReservationUpdate_setsAutoRenew(t *testing.T) {
	for _, value := range []string{"false", "true"} {
		_, _, err := runCLI(t, []string{"reservation", "update", "region-a:res-1", "--auto-renew=" + value}, []expectedRequest{{
			method: http.MethodPatch, path: "/reservations/region-a:res-1", status: 200,
			bodyJSON: `{"auto_renew":` + value + `}`, response: macReservationFixture(`"m4-10c-16g"`, m4Capacity),
		}})
		require.NoError(t, err)
	}

	_, _, err := runCLI(t, []string{"reservation", "update", "region-a:res-1"}, nil)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Equal(t, "error: reservation update needs --auto-renew=true or --auto-renew=false; example: daytona reservation update RESERVATION_ID --auto-renew=false\n", stderrOf(err))

	help, _, err := runCLI(t, []string{"reservation", "update", "--help"}, nil)
	require.NoError(t, err)
	require.Contains(t, squashSpaces(help), "Turn auto-renew on or off (PATCH /reservations/{id}). An auto-renewing reservation renews near period_end only while a sandbox is in use on it; stopped and paused sandboxes do not renew it. With auto-renew off, the reservation ends at period_end.")
	require.Contains(t, help, "Examples:\n  daytona reservation update RESERVATION_ID --auto-renew=false\n  daytona reservation update RESERVATION_ID --auto-renew=true")
}
