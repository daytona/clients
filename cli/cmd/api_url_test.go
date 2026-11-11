// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"testing"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/stretchr/testify/require"
)

// whoamiAPIURL runs `whoami -o json` and returns the api_url it reports.
func whoamiAPIURL(t *testing.T, d string, env map[string]string, args ...string) string {
	t.Helper()
	out, errb, code := run(t, d, env, append([]string{"whoami", "-o", "json"}, args...)...)
	require.Equal(t, 0, code, errb)
	var got struct {
		APIURL string `json:"api_url"`
	}
	require.NoError(t, json.Unmarshal([]byte(out), &got), out)
	return got.APIURL
}

// The API URL resolves like the SDKs': DAYTONA_V2_API_URL, then the
// production default — a blank, whitespace-only or slash-only value counts as
// unset and trailing slashes are dropped. The --api-url flag sits above the
// variable. DAYTONA_API_URL belongs to 0.x and is never read.
func TestAPIURL_resolutionMatchesTheSDKs(t *testing.T) {
	require.Equal(t, "https://api.daytona.com", app.DefaultAPIURL)
	cases := []struct {
		name string
		env  map[string]string
		args []string
		want string
	}{
		{name: "nothing set uses the default", want: app.DefaultAPIURL},
		{name: "DAYTONA_API_URL is not read", env: map[string]string{"DAYTONA_API_URL": "http://v0.test/"}, want: app.DefaultAPIURL},
		{name: "DAYTONA_V2_API_URL", env: map[string]string{"DAYTONA_V2_API_URL": "http://v2.test//", "DAYTONA_API_URL": "http://v0.test"}, want: "http://v2.test"},
		{name: "blank DAYTONA_V2_API_URL falls through to the default", env: map[string]string{"DAYTONA_V2_API_URL": "  ", "DAYTONA_API_URL": "http://v0.test"}, want: app.DefaultAPIURL},
		{name: "slash-only values fall through to the default", env: map[string]string{"DAYTONA_V2_API_URL": "/"}, want: app.DefaultAPIURL},
		{name: "--api-url wins over the variable", env: map[string]string{"DAYTONA_V2_API_URL": "http://v2.test"}, args: []string{"--api-url", " http://flag.test/ "}, want: "http://flag.test"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			require.Equal(t, tc.want, whoamiAPIURL(t, cfgDir(t), tc.env, tc.args...))
		})
	}
}

// A login saves the URL it resolved, so a profile made against a test
// environment keeps pointing at it once the variable is gone. An override
// cannot send its saved credential to another API.
func TestAPIURL_loginSavesTheResolvedURLInTheProfile(t *testing.T) {
	f, other := newFakeAPI(t), newFakeAPI(t)
	d := cfgDir(t)
	_, errb, code := run(t, d, map[string]string{"DAYTONA_V2_API_URL": f.srv.URL + "/"}, "login", "--token", "dtn_k")
	require.Equal(t, 0, code, errb)
	require.Contains(t, errb, f.srv.URL)

	require.Equal(t, f.srv.URL, whoamiAPIURL(t, d, nil))
	_, errb, code = run(t, d, map[string]string{"DAYTONA_V2_API_URL": other.srv.URL}, "whoami")
	require.Equal(t, cerr.ExitUsage, code, errb)
	require.Empty(t, other.requests)
	require.Equal(t, f.srv.URL, whoamiAPIURL(t, d, map[string]string{"DAYTONA_API_URL": "http://v0.test"}))

	d2 := cfgDir(t)
	_, errb, code = run(t, d2, nil, "login", "--token", "dtn_k")
	require.Equal(t, 0, code, errb)
	store, err := config.Open(d2)
	require.NoError(t, err)
	cfg, err := store.LoadConfig()
	require.NoError(t, err)
	profile, ok := cfg.Profile("default")
	require.True(t, ok)
	require.Equal(t, app.DefaultAPIURL, profile.APIURL)
}
