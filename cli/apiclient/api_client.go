// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package apiclient

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"

	apiclient "github.com/daytona/clients/api-client-go"
	"github.com/daytona/clients/cli/auth"
	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/internal"

	log "github.com/sirupsen/logrus"
)

type versionCheckTransport struct {
	transport http.RoundTripper
}

var versionMismatchWarningOnce sync.Once

func (t *versionCheckTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := t.transport.RoundTrip(req)
	if resp != nil {
		// Check version mismatch on all responses, not just errors
		checkVersionsMismatch(resp)
	}
	return resp, err
}

const DaytonaSourceHeader = "X-Daytona-Source"
const API_VERSION_HEADER = "X-Daytona-Api-Version"

func checkVersionsMismatch(res *http.Response) {
	// If the CLI is running in a structured output mode (e.g. json/yaml),
	// avoid printing human-readable warnings that could break consumers.
	if internal.SuppressVersionMismatchWarning {
		return
	}

	serverVersion := res.Header.Get(API_VERSION_HEADER)
	if serverVersion == "" {
		return
	}

	// Trim "v" prefix from both versions for comparison
	cliVersion := strings.TrimPrefix(internal.Version, "v")
	apiVersion := strings.TrimPrefix(serverVersion, "v")

	if cliVersion == "0.0.0-dev" || cliVersion == apiVersion {
		return
	}

	if compareVersions(cliVersion, apiVersion) >= 0 {
		return
	}

	// The API being ahead only means a newer CLI *might* exist: not every API
	// release ships a CLI release. Only tell the user to upgrade when there is
	// actually a newer CLI to upgrade to.
	versionMismatchWarningOnce.Do(func() {
		latestVersion, err := latestCliVersion(res.Request.Context())
		if err != nil {
			log.Debug(err)
			return
		}

		if compareVersions(cliVersion, latestVersion) >= 0 {
			return
		}

		log.Warn(fmt.Sprintf("Daytona CLI v%s is outdated, the latest version is v%s.\nUpgrade using 'brew upgrade daytonaio/cli/daytona' or by downloading the latest version from https://github.com/daytona/clients/releases.", cliVersion, latestVersion))
	})
}

// compareVersions compares two semver strings
// Returns: -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
// A pre-release (e.g. 0.216.0-alpha1) sorts before its release (0.216.0).
func compareVersions(v1, v2 string) int {
	core1, pre1, _ := strings.Cut(v1, "-")
	core2, pre2, _ := strings.Cut(v2, "-")

	if c := compareVersionCores(core1, core2); c != 0 {
		return c
	}

	switch {
	case pre1 == pre2:
		return 0
	case pre1 == "":
		return 1
	case pre2 == "":
		return -1
	case pre1 < pre2:
		return -1
	default:
		return 1
	}
}

func compareVersionCores(v1, v2 string) int {
	parts1 := strings.Split(v1, ".")
	parts2 := strings.Split(v2, ".")

	maxLen := len(parts1)
	if len(parts2) > maxLen {
		maxLen = len(parts2)
	}

	for i := 0; i < maxLen; i++ {
		var n1, n2 int
		if i < len(parts1) {
			_, _ = fmt.Sscanf(parts1[i], "%d", &n1)
		}
		if i < len(parts2) {
			_, _ = fmt.Sscanf(parts2[i], "%d", &n2)
		}

		if n1 < n2 {
			return -1
		}
		if n1 > n2 {
			return 1
		}
	}

	return 0
}

func GetApiClient(profile *config.Profile, defaultHeaders map[string]string) (*apiclient.APIClient, error) {
	c, err := config.GetConfig()
	if err != nil {
		return nil, err
	}

	var activeProfile config.Profile
	if profile == nil {
		var err error
		activeProfile, err = c.GetActiveProfile()
		if err != nil {
			return nil, err
		}
	} else {
		activeProfile = *profile
	}

	// Refresh before the default headers are applied, and re-read the profile the refresh
	// wrote, so the returned client always carries the current token and organization.
	// RefreshTokenIfNeeded acts on the active profile, which is not necessarily the one
	// passed in, so an explicitly supplied profile is used exactly as given.
	if profile == nil && activeProfile.Api.Key == nil && activeProfile.Api.Token != nil {
		err = auth.RefreshTokenIfNeeded(context.Background())
		if err != nil {
			return nil, err
		}

		c, err = config.GetConfig()
		if err != nil {
			return nil, err
		}

		activeProfile, err = c.GetActiveProfile()
		if err != nil {
			return nil, err
		}
	}

	var newApiClient *apiclient.APIClient

	serverUrl := activeProfile.Api.Url

	clientConfig := apiclient.NewConfiguration()
	clientConfig.Servers = apiclient.ServerConfigurations{
		{
			URL: serverUrl,
		},
	}

	if activeProfile.Api.Key != nil {
		clientConfig.AddDefaultHeader("Authorization", "Bearer "+*activeProfile.Api.Key)
	} else if activeProfile.Api.Token != nil {
		clientConfig.AddDefaultHeader("Authorization", "Bearer "+activeProfile.Api.Token.AccessToken)

		if activeProfile.ActiveOrganizationId != nil {
			clientConfig.AddDefaultHeader("X-Daytona-Organization-ID", *activeProfile.ActiveOrganizationId)
		}
	}

	clientConfig.AddDefaultHeader(DaytonaSourceHeader, "cli")

	for headerKey, headerValue := range defaultHeaders {
		clientConfig.AddDefaultHeader(headerKey, headerValue)
	}

	newApiClient = apiclient.NewAPIClient(clientConfig)

	newApiClient.GetConfig().HTTPClient = &http.Client{
		Transport: &versionCheckTransport{
			transport: http.DefaultTransport,
		},
	}

	return newApiClient, nil
}
