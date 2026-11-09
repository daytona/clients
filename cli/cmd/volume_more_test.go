// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// The server chooses a volume's type when --type is omitted, so the help
// names no client-side default for it or for --size-gib.
func TestVolumeCreate_helpNamesNoClientDefaults(t *testing.T) {
	help, _, err := runCLI(t, []string{"volume", "create", "--help"}, nil)
	require.NoError(t, err)
	require.Contains(t, help, "default: chosen by the server")
	require.NotContains(t, help, `(default "disk")`)
	require.NotContains(t, help, "(default 100)")
}

func TestVolumeCreate_rejectsShapesTheServerWouldRefuse(t *testing.T) {
	cases := map[string][]string{
		"size-gib on a bucket volume":     {"volume", "create", "v", "--size-gib", "50", "--type", "bucket"},
		"customer bucket without creds":   {"volume", "create", "v", "--type", "bucket", "--bucket", "corp"},
		"customer bucket with half creds": {"volume", "create", "v", "--type", "bucket", "--bucket", "corp", "--access-key-secret", "K"},
		"bucket options without a type":   {"volume", "create", "v", "--bucket", "corp", "--access-key-secret", "K", "--secret-key-secret", "S"},
		"bucket options on a disk volume": {"volume", "create", "v", "--type", "disk", "--bucket", "corp", "--access-key-secret", "K", "--secret-key-secret", "S"},
	}
	for name, args := range cases {
		t.Run(name, func(t *testing.T) {
			_, _, err := runCLI(t, args, nil)
			require.Error(t, err)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), err.Error())
		})
	}
	bodyPath := filepath.Join(t.TempDir(), "volume.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"type":"bucket","bucket":{"name":"x","origin":"invalid-origin","credentials":{"access_key_secret":"K","secret_key_secret":"S"}}}`), 0o600))
	_, _, err := runCLI(t, []string{"volume", "create", "v", "--file", bodyPath}, nil)
	require.Error(t, err)
	require.Contains(t, err.Error(), "origin")
}

func TestVolumeCreate_externalBucketBody(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"volume", "create", "data", "--type", "bucket", "--bucket", "corp-data", "--endpoint", "https://s3.example.com", "--bucket-region", "region-a", "--prefix", "team-a/", "--path-style", "--access-key-secret", "BUCKET_KEY", "--secret-key-secret", "BUCKET_SECRET",
	}, []expectedRequest{
		{method: http.MethodPost, path: "/volumes", status: 201, bodyJSON: `{"bucket":{"credentials":{"access_key_secret":"BUCKET_KEY","secret_key_secret":"BUCKET_SECRET"},"endpoint":"https://s3.example.com","name":"corp-data","origin":"external","path_style":true,"prefix":"team-a/","region":"region-a"},"name":"data","type":"bucket"}`, response: bucketVolumeFixture("55555555-5555-5555-5555-555555555555")},
	})
	require.NoError(t, err)
	require.Equal(t, "data", requireJSONStdout(t, stdout)["name"])
}

func TestVolumeCreate_platformBucketBody(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"volume", "create", "data", "--type", "bucket"}, []expectedRequest{
		{method: http.MethodPost, path: "/volumes", status: 201, bodyJSON: `{"name":"data","type":"bucket"}`, response: bucketVolumeFixture("55555555-5555-5555-5555-555555555555")},
	})
	require.NoError(t, err)
	require.Equal(t, "data", requireJSONStdout(t, stdout)["name"])
}

func TestVolumeCreate_fileBucketOverride(t *testing.T) {
	bodyPath := filepath.Join(t.TempDir(), "volume.json")
	require.NoError(t, os.WriteFile(bodyPath, []byte(`{"type":"bucket","bucket":{"name":"from-file","endpoint":"https://old.example.com","region":"region-b","credentials":{"access_key_secret":"OLD_KEY","secret_key_secret":"OLD_SECRET"}}}`), 0o600))
	stdout, _, err := runCLI(t, []string{
		"volume", "create", "data", "--file", bodyPath, "--bucket", "corp-data", "--endpoint", "https://s3.example.com", "--bucket-region", "region-a", "--access-key-secret", "BUCKET_KEY", "--secret-key-secret", "BUCKET_SECRET",
	}, []expectedRequest{
		{method: http.MethodPost, path: "/volumes", status: 201, bodyJSON: `{"bucket":{"credentials":{"access_key_secret":"BUCKET_KEY","secret_key_secret":"BUCKET_SECRET"},"endpoint":"https://s3.example.com","name":"corp-data","origin":"external","region":"region-a"},"name":"data","type":"bucket"}`, response: bucketVolumeFixture("55555555-5555-5555-5555-555555555555")},
	})
	require.NoError(t, err)
	require.Equal(t, "data", requireJSONStdout(t, stdout)["name"])
}

func TestVolumeList_liveVolumes(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"volume", "list"}, []expectedRequest{
		{method: http.MethodGet, path: "/volumes", query: "limit=100", status: 200, response: `{"volumes":[` + diskVolumeFixture("55555555-5555-5555-5555-555555555555") + `]}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, requireJSONStdout(t, stdout))
}

func TestVolumeKV_bucketFields(t *testing.T) {
	var volume gen.Volume
	require.NoError(t, json.Unmarshal([]byte(bucketVolumeFixture("55555555-5555-5555-5555-555555555555")), &volume))
	kv := volumeKV(volume)
	require.Contains(t, kv, [2]string{"bucket_name", "corp-data"})
	require.Contains(t, kv, [2]string{"bucket_origin", "external"})
	require.Contains(t, kv, [2]string{"bucket_endpoint", "https://s3.example.com"})
	require.Contains(t, kv, [2]string{"bucket_region", "region-a"})
	require.Contains(t, kv, [2]string{"bucket_prefix", "team-a/"})
	require.Contains(t, kv, [2]string{"bucket_access_key_secret", "BUCKET_KEY"})
	require.Contains(t, kv, [2]string{"bucket_secret_key_secret", "BUCKET_SECRET"})
}

func TestVolumeDelete_requiresYes(t *testing.T) {
	_, _, err := runCLI(t, []string{"volume", "delete", "55555555-5555-5555-5555-555555555555"}, nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}
