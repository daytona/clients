// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package app

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/config"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/ui"
	"github.com/stretchr/testify/require"
)

func TestRequireProject_usesResolvedOrgOverrideToFindRememberedProject(t *testing.T) {
	t.Parallel()
	const (
		orgID     = "11111111-1111-1111-1111-111111111111"
		projectID = "22222222-2222-2222-2222-222222222222"
	)
	a := &App{
		Profile: &config.Profile{OrgID: "Acme", ProjectByOrg: map[string]string{orgID: projectID}},
		Format:  output.FormatJSON,
		Mode:    ui.Mode{},
		OrgResolver: func(context.Context, string) (string, error) {
			return orgID, nil
		},
		orgFromOverride: true,
	}

	got, err := a.RequireProject(context.Background(), nil)
	require.NoError(t, err)
	require.Equal(t, projectID, got)
	require.Equal(t, orgID, a.Profile.OrgID)
	require.Equal(t, projectID, a.Profile.ProjectByOrg[orgID])
	_, stale := a.Profile.ProjectByOrg["Acme"]
	require.False(t, stale)
}

func TestWorkloadClient_resolvesProjectNameBeforeReturningStaticClient(t *testing.T) {
	t.Parallel()
	const (
		orgID     = "11111111-1111-1111-1111-111111111111"
		projectID = "22222222-2222-2222-2222-222222222222"
	)
	a := &App{
		Globals:          Globals{},
		Profile:          &config.Profile{Name: "default", APIURL: "https://api.example", OrgID: orgID, ProjectByOrg: map[string]string{orgID: "my-proj"}},
		Format:           output.FormatJSON,
		Mode:             ui.Mode{},
		CredentialSource: "flag",
		credentialFn:     staticCred("static-bearer"),
		ProjectResolver: func(_ context.Context, gotOrgID, ref string) (string, error) {
			require.Equal(t, orgID, gotOrgID)
			require.Equal(t, "my-proj", ref)
			return projectID, nil
		},
	}

	client, gotProject, err := a.WorkloadClient(context.Background(), nil)
	require.NoError(t, err)
	require.Equal(t, projectID, gotProject)
	require.Equal(t, projectID, a.Profile.ProjectByOrg[orgID])
	require.Equal(t, "static-bearer", client.Credential)
}

func TestWorkloadClient_passesResolvedProjectIDToPlatformTokenExchange(t *testing.T) {
	t.Parallel()
	const (
		orgID     = "11111111-1111-1111-1111-111111111111"
		projectID = "22222222-2222-2222-2222-222222222222"
	)
	calledWith := ""
	a := &App{
		Profile:          &config.Profile{Name: "default", APIURL: "https://api.example", OrgID: orgID, ProjectByOrg: map[string]string{orgID: "my-proj"}},
		Format:           output.FormatJSON,
		Mode:             ui.Mode{},
		CredentialSource: "profile-oidc",
		ProjectResolver: func(_ context.Context, gotOrgID, ref string) (string, error) {
			require.Equal(t, orgID, gotOrgID)
			require.Equal(t, "my-proj", ref)
			return projectID, nil
		},
		PlatformTokenFn: func(_ context.Context, project, rejected string) (string, bool, error) {
			calledWith = project
			require.Empty(t, rejected)
			return "platform-token", true, nil
		},
		WorkloadCredentialFn: func(context.Context, string, string) (string, error) {
			return "", errors.New("project key fallback should not run")
		},
	}

	client, gotProject, err := a.WorkloadClient(context.Background(), nil)
	require.NoError(t, err)
	require.Equal(t, projectID, gotProject)
	require.Equal(t, projectID, calledWith)
	require.Equal(t, "platform-token", client.Credential)
}

func TestRequireProject_returnsUsageErrorForAmbiguousProjectName(t *testing.T) {
	t.Parallel()
	a := &App{
		Profile: &config.Profile{OrgID: "11111111-1111-1111-1111-111111111111", ProjectByOrg: map[string]string{"11111111-1111-1111-1111-111111111111": "My-Project"}},
		Format:  output.FormatJSON,
		Mode:    ui.Mode{},
		ProjectResolver: func(context.Context, string, string) (string, error) {
			return "", cerr.Usage("project name %q is ambiguous (p1, p2)", "My-Project").WithHint("pass the id instead")
		},
	}

	_, err := a.RequireProject(context.Background(), nil)
	require.Error(t, err)
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

// SigningClient (sandbox cp's pre-signed file URLs) follows ToolboxClient's
// credential rule but stays on the original API's base URL: a login's
// platform token is swapped for the login's project KEY, while an explicit or
// profile-token credential is reused as is.
func TestSigningClient_oidcLoginMintsProjectKeyOnOriginalBaseURL(t *testing.T) {
	t.Parallel()
	const projectID = "22222222-2222-2222-2222-222222222222"
	apiClient := api.New("https://api.example", "platform-token", "test")
	apiClient.Debug = func(string, ...any) {}
	mintedFor := ""
	a := &App{
		Format:           output.FormatJSON,
		Mode:             ui.Mode{},
		CredentialSource: "profile-oidc",
		PlatformTokenFn: func(context.Context, string, string) (string, bool, error) {
			return "", false, errors.New("the CLI signs with a project key: the exchange must not run")
		},
		WorkloadCredentialFn: func(_ context.Context, project, rejected string) (string, error) {
			mintedFor = project
			require.Empty(t, rejected)
			return "minted-project-key", nil
		},
	}

	signing, err := a.SigningClient(context.Background(), apiClient, projectID)
	require.NoError(t, err)
	require.NotSame(t, apiClient, signing)
	require.Equal(t, projectID, mintedFor)
	require.Equal(t, apiClient.BaseURL, signing.BaseURL, "the sign route uses the original API endpoint")
	require.Equal(t, "minted-project-key", signing.Credential)
	require.False(t, signing.RetryForbidden, "a fresh key is authoritative: no re-mint loop")
	require.Nil(t, signing.OnUnauthorized)
	require.NotNil(t, signing.Debug, "the API client's debug sink is carried over")
	// The API client is untouched: the platform token stays its bearer.
	require.Equal(t, "platform-token", apiClient.Credential)
}

func TestSigningClient_oidcLoginReusesCachedProjectKey(t *testing.T) {
	t.Parallel()
	const projectID = "22222222-2222-2222-2222-222222222222"
	apiClient := api.New("https://api.example", "platform-token", "test")
	a := &App{
		Format:           output.FormatJSON,
		Mode:             ui.Mode{},
		CredentialSource: "profile-oidc",
		PlatformTokenFn: func(context.Context, string, string) (string, bool, error) {
			return "", false, errors.New("the exchange must not run")
		},
		CachedWorkloadKeyFn: func(project string) string {
			require.Equal(t, projectID, project)
			return "cached-project-key"
		},
		WorkloadCredentialFn: func(context.Context, string, string) (string, error) {
			return "", errors.New("a cached key must not be re-minted")
		},
	}

	signing, err := a.SigningClient(context.Background(), apiClient, projectID)
	require.NoError(t, err)
	require.Equal(t, apiClient.BaseURL, signing.BaseURL)
	require.Equal(t, "cached-project-key", signing.Credential)
	require.True(t, signing.RetryForbidden, "a cached key may be stale: a 403 re-mints once")
	require.NotNil(t, signing.OnUnauthorized)
}

func TestSigningClient_oidcLoginSurfacesMintError(t *testing.T) {
	t.Parallel()
	apiClient := api.New("https://api.example", "platform-token", "test")
	a := &App{
		Format:           output.FormatJSON,
		Mode:             ui.Mode{},
		CredentialSource: "profile-oidc",
		PlatformTokenFn: func(context.Context, string, string) (string, bool, error) {
			return "platform-token", true, nil
		},
		WorkloadCredentialFn: func(context.Context, string, string) (string, error) {
			return "", cerr.New(cerr.ExitAuth, "ceiling read failed")
		},
	}

	signing, err := a.SigningClient(context.Background(), apiClient, "22222222-2222-2222-2222-222222222222")
	require.Error(t, err)
	require.Nil(t, signing)
	require.Equal(t, cerr.ExitAuth, cerr.ExitCode(err))
}

func TestSigningClient_returnsAPIClientUnchangedWithoutALoginSourcedBearer(t *testing.T) {
	t.Parallel()
	const projectID = "22222222-2222-2222-2222-222222222222"
	mustNotMint := func(context.Context, string, string) (string, error) {
		return "", errors.New("no project key must be minted")
	}
	platformToken := func(context.Context, string, string) (string, bool, error) {
		return "platform-token", true, nil
	}
	cases := []struct {
		name    string
		app     *App
		project string
	}{
		{"--api-key flag", &App{CredentialSource: "flag", PlatformTokenFn: platformToken, WorkloadCredentialFn: mustNotMint}, projectID},
		{"DAYTONA_API_KEY env", &App{CredentialSource: "env", PlatformTokenFn: platformToken, WorkloadCredentialFn: mustNotMint}, projectID},
		{"profile with a static token", &App{CredentialSource: "profile-token", PlatformTokenFn: platformToken, WorkloadCredentialFn: mustNotMint}, projectID},
		{"deployment without a platform-token issuer", &App{CredentialSource: "profile-oidc", PlatformTokenFn: nil, WorkloadCredentialFn: mustNotMint}, projectID},
		{"no project selected", &App{CredentialSource: "profile-oidc", PlatformTokenFn: platformToken, WorkloadCredentialFn: mustNotMint}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			apiClient := api.New("https://api.example", "static-bearer", "test")
			signing, err := c.app.SigningClient(context.Background(), apiClient, c.project)
			require.NoError(t, err)
			require.Same(t, apiClient, signing, "the API client is returned as is")
			require.Equal(t, "static-bearer", signing.Credential)
		})
	}
}

func TestSameAPIURL_ignoresSchemeAndHostCase(t *testing.T) {
	t.Parallel()
	require.True(t, sameAPIURL("https://API.Example.com", "HTTPS://api.example.COM"))
	require.True(t, sameAPIURL("http://localhost:8080/v2", "HTTP://LOCALHOST:8080/v2"))
	require.False(t, sameAPIURL("https://api.example.com", "http://api.example.com"))
	require.False(t, sameAPIURL("https://api.example.com", "https://api.example.org"))
	require.False(t, sameAPIURL("http://localhost:8080", "http://localhost:8081"))
	require.False(t, sameAPIURL("https://api.example.com/a", "https://api.example.com/b"))
}

func TestInfo_keepsServerTextOnOneLine(t *testing.T) {
	var errb bytes.Buffer
	a := &App{Stderr: &errb}
	a.Info("deleted %s", "box\nerror: forged")
	if got := errb.String(); got != "deleted box error: forged\n" {
		t.Fatalf("got %q", got)
	}
}
