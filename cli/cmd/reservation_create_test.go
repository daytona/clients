// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestReservationCreate_sendsModelRegionAndKey(t *testing.T) {
	stdout, _, err := runCLIWithOutput(t, "table", []string{
		"reservation", "create", "--yes", "--region", "region-a", "--model", "m4pro-12c-24g", "--auto-renew=false", "--idempotency-key", "nightly-mac",
	}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/reservations",
		status:   201,
		bodyJSON: `{"region_id":"region-a","model":"m4pro-12c-24g","auto_renew":false}`,
		response: macReservationFixture(`"m4pro-12c-24g"`, m4proCapacity),
		check: func(t *testing.T, r *http.Request, _ []byte) {
			require.Equal(t, "nightly-mac", r.Header.Get("Idempotency-Key"))
		},
	}})
	require.NoError(t, err)
	rows := objectRows(t, stdout)
	require.Equal(t, "m4pro-12c-24g", rows["model"])
	require.Equal(t, "5", rows["cores_used"])
	require.Equal(t, "12", rows["cores_total"])
	require.Equal(t, "8192", rows["memory_mib_used"])
	require.Equal(t, "24576", rows["memory_mib_total"])
	require.Equal(t, "81920", rows["disk_mib_used"])
	require.Equal(t, "122880", rows["disk_mib_total"])
}

// Without --model the body omits it (the region's default model); without
// --idempotency-key each invocation sends one fresh key.
func TestReservationCreate_defaultsAndFileRegion(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "reservation.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"region_id":"region-a"}`), 0o600))
	var key string
	_, _, err := runCLI(t, []string{"reservation", "create", "--yes", "--file", bodyPath}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/reservations",
		status:   201,
		bodyJSON: `{"region_id":"region-a"}`,
		response: macReservationFixture(`"m4-10c-16g"`, m4Capacity),
		check: func(t *testing.T, r *http.Request, _ []byte) {
			key = r.Header.Get("Idempotency-Key")
		},
	}})
	require.NoError(t, err)
	require.NotEmpty(t, key)

	// An explicit --region wins over the file's.
	_, _, err = runCLI(t, []string{"reservation", "create", "--yes", "--file", bodyPath, "--region", "region-b"}, []expectedRequest{{
		method:   http.MethodPost,
		path:     "/reservations",
		status:   201,
		bodyJSON: `{"region_id":"region-b"}`,
		response: macReservationFixture(`"m4-10c-16g"`, m4Capacity),
		check: func(t *testing.T, r *http.Request, _ []byte) {
			require.NotEmpty(t, r.Header.Get("Idempotency-Key"))
			require.NotEqual(t, key, r.Header.Get("Idempotency-Key"))
		},
	}})
	require.NoError(t, err)
}

func TestReservationCreate_needsRegion(t *testing.T) {
	_, _, err := runCLI(t, []string{"reservation", "create", "--model", "m4-10c-16g"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Equal(t, "error: reservation create needs --region (the region whose Macs to reserve); example: daytona reservation create --region '<region>'\n", stderrOf(err))
}

// --count buys through POST /reservations/bulk: the table is the
// `reservation list` table, the count line goes to stderr, and JSON is the
// API body.
func TestReservationCreate_countBuysInBulk(t *testing.T) {
	// The second reservation carries an explicit null, which JSON output keeps.
	batch := `{"requested":3,"reserved":2,"reservations":[` + macReservationFixture(`"m4pro-12c-24g"`, m4proCapacity) + `,` +
		strings.Replace(macReservationFixture(`"m4pro-12c-24g"`, m4proCapacity), `"model":`, `"ended_at":null,"model":`, 1) + `]}`
	bulk := expectedRequest{
		method:   http.MethodPost,
		path:     "/reservations/bulk",
		status:   201,
		bodyJSON: `{"region_id":"region-a","model":"m4pro-12c-24g","count":3,"min_count":2}`,
		response: batch,
		check: func(t *testing.T, r *http.Request, _ []byte) {
			require.Equal(t, "nightly-macs", r.Header.Get("Idempotency-Key"))
		},
	}
	args := []string{"reservation", "create", "--yes", "--region", "region-a", "--model", "m4pro-12c-24g", "--count", "3", "--min-count", "2", "--idempotency-key", "nightly-macs"}
	stdout, stderr, err := runCLIWithOutput(t, "table", args, []expectedRequest{bulk})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 3)
	require.Equal(t, []string{"ID", "REGION", "MODEL", "STATUS", "CORES_USED", "CORES_TOTAL", "MEMORY_MIB_USED", "MEMORY_MIB_TOTAL", "DISK_MIB_USED", "DISK_MIB_TOTAL", "AUTO_RENEW", "PERIOD_END"}, strings.Fields(lines[0]))
	require.Equal(t, []string{"region-a:res-1", "region-a", "m4pro-12c-24g", "active", "5", "12", "8192", "24576", "81920", "122880", "true", "2026-10-02T00:00:00Z"}, strings.Fields(lines[1]))
	require.Equal(t, "reserved 2 of 3 Macs\n", stderr)

	stdout, _, err = runCLI(t, args, []expectedRequest{bulk})
	require.NoError(t, err)
	require.JSONEq(t, batch, stdout)

	// Without --idempotency-key each invocation sends one fresh key.
	var keys []string
	fresh := expectedRequest{
		method: http.MethodPost, path: "/reservations/bulk", status: 201, bodyJSON: `{"region_id":"region-a","count":3}`, response: batch,
		check: func(t *testing.T, r *http.Request, _ []byte) { keys = append(keys, r.Header.Get("Idempotency-Key")) },
	}
	for range 2 {
		_, _, err = runCLI(t, []string{"reservation", "create", "--yes", "--region", "region-a", "--count", "3"}, []expectedRequest{fresh})
		require.NoError(t, err)
	}
	require.Len(t, keys, 2)
	require.NotEmpty(t, keys[0])
	require.NotEqual(t, keys[0], keys[1])
}

func TestReservationCreate_refusesBadCounts(t *testing.T) {
	for _, tc := range []struct {
		args   []string
		stderr string
	}{
		{[]string{"--min-count", "2"}, "error: --min-count needs --count; example: daytona reservation create --region '<region>' --count 5 --min-count 2\n"},
		{[]string{"--count", "0"}, "error: --count must be between 1 and 100; example: daytona reservation create --region '<region>' --count 3\n"},
		{[]string{"--count", "101"}, "error: --count must be between 1 and 100; example: daytona reservation create --region '<region>' --count 3\n"},
		{[]string{"--count", "5", "--min-count", "6"}, "error: --min-count must be between 1 and --count (5); example: daytona reservation create --region '<region>' --count 5 --min-count 2\n"},
		{[]string{"--count", "5", "--min-count", "0"}, "error: --min-count must be between 1 and --count (5); example: daytona reservation create --region '<region>' --count 5 --min-count 2\n"},
	} {
		args := append([]string{"reservation", "create", "--region", "region-a"}, tc.args...)
		_, _, err := runCLI(t, args, nil)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), "%v", tc.args)
		require.Equal(t, tc.stderr, stderrOf(err), "%v", tc.args)
	}
}

// --dry-run reads the region's GET /mac-catalog and buys nothing: the model
// defaults to the region's default model.
func TestReservationCreate_dryRunPricesFromTheCatalog(t *testing.T) {
	routing := expectedRequest{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()}
	catalog := expectedRequest{method: http.MethodGet, path: "/mac-catalog", query: "region_ids=region-b", status: 200, response: macCatalogFixture()}

	stdout, _, err := runCLIWithOutput(t, "table", []string{"reservation", "create", "--region", "region-b", "--count", "3", "--dry-run"}, []expectedRequest{routing, catalog})
	require.NoError(t, err)
	require.Equal(t, "would reserve 3 x m4-10c-16g in region-b: $32.43 per Mac per 24 h, $97.29 for the first period; 4 available now\n", stdout)

	stdout, _, err = runCLIWithOutput(t, "table", []string{"reservation", "create", "--region", "region-b", "--model", "m4pro-12c-24g", "--dry-run"}, []expectedRequest{routing, catalog})
	require.NoError(t, err)
	require.Equal(t, "would reserve 1 x m4pro-12c-24g in region-b: no price in this region; 0 available now\n", stdout)

	stdout, _, err = runCLI(t, []string{"reservation", "create", "--region", "region-b", "--model", "m4-10c-16g", "--count", "3", "--dry-run"}, []expectedRequest{routing, catalog})
	require.NoError(t, err)
	require.JSONEq(t, `{"region":"region-b","model":"m4-10c-16g","count":3,"available":4,"period_cents":3243,"total_cents":9729}`, stdout)

	stdout, _, err = runCLI(t, []string{"reservation", "create", "--region", "region-b", "--model", "m4pro-12c-24g", "--dry-run"}, []expectedRequest{routing, catalog})
	require.NoError(t, err)
	require.JSONEq(t, `{"region":"region-b","model":"m4pro-12c-24g","count":1,"available":0,"period_cents":null,"total_cents":null}`, stdout)

	_, _, err = runCLI(t, []string{"reservation", "create", "--region", "region-b", "--model", "m9-1c-1g", "--dry-run"}, []expectedRequest{routing, catalog})
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.Equal(t, "error: unknown Mac model \"m9-1c-1g\" in region region-b (see `daytona mac-model list --region region-b`)\n", stderrOf(err))
}

func TestReservationCreate_help(t *testing.T) {
	help, _, err := runCLI(t, []string{"reservation", "create", "--help"}, nil)
	require.NoError(t, err)
	text := squashSpaces(help)
	for _, line := range []string{
		"--count N how many Macs to reserve, 1 to 100 (calls POST /reservations/bulk; default: one Mac through POST /reservations)",
		"--min-count M the fewest Macs to accept when fewer than --count are free or fit under the organization's Mac reservation limit (default: --count, all or nothing)",
		"--dry-run print the price and the free Macs from GET /mac-catalog and reserve nothing",
		"With --count, reserve several Macs of one model in one request (POST /reservations/bulk). The SDK twin is reservations.createBulk(…).",
		"daytona reservation create --region '<region>' --model '<model>' --count 3 --idempotency-key nightly-macs",
		"daytona reservation create --region '<region>' --count 5 --min-count 2",
		"daytona reservation create --region '<region>' --model '<model>' --count 3 --dry-run",
	} {
		require.Contains(t, text, line)
	}
	require.NotContains(t, text, "(default 0)")

	parent, _, err := runCLI(t, []string{"reservation", "--help"}, nil)
	require.NoError(t, err)
	for _, line := range []string{
		"daytona reservation create --region '<region>' --count 3",
		"daytona reservation renew RESERVATION_ID",
		"daytona reservation update RESERVATION_ID --auto-renew=false",
		"Charge and renew a Mac reservation for another period",
		"Turn a Mac reservation's auto-renew on or off",
	} {
		require.Contains(t, parent, line)
	}
}
