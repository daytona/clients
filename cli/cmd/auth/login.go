// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/daytona/clients/cli/auth"
	"github.com/daytona/clients/cli/cmd/common"
	"github.com/daytona/clients/cli/config"
	"github.com/daytona/clients/cli/internal"
	view_common "github.com/daytona/clients/cli/views/common"
	"github.com/pkg/browser"
	"github.com/spf13/cobra"
	"golang.org/x/oauth2"
)

var LoginCmd = &cobra.Command{
	Use:     "login",
	Short:   "Log in to Daytona",
	Args:    cobra.NoArgs,
	GroupID: internal.USER_GROUP,
	RunE: func(cmd *cobra.Command, args []string) error {
		ctx := context.Background()

		if apiKeyFlag != "" {
			return updateProfileWithLogin(nil, &apiKeyFlag)
		}

		items := []view_common.SelectItem{
			{Title: "Login with Browser", Desc: "Authenticate using OAuth in your browser"},
			{Title: "Set Daytona API Key", Desc: "Authenticate using Daytona API key"},
		}

		choice, err := view_common.Select("Select Authentication Method", items)
		if err != nil {
			return fmt.Errorf("error running selection prompt: %w", err)
		}

		if choice == "" {
			return nil
		}

		if choice == "Set Daytona API Key" {
			// Prompt for API key
			apiKey, err := view_common.PromptForInput("", "Enter your Daytona API key", "You can find it in the Daytona dashboard - https://app.daytona.io/dashboard")
			if err != nil {
				return err
			}
			return updateProfileWithLogin(nil, &apiKey)
		}

		token, err := login(ctx)
		if err != nil {
			return err
		}

		return updateProfileWithLogin(token, nil)
	},
}

var (
	apiKeyFlag string
)

func init() {
	LoginCmd.Flags().StringVar(&apiKeyFlag, "api-key", "", "API key to use for authentication")
}

func updateProfileWithLogin(tokenConfig *config.Token, apiKey *string) error {
	c, err := config.GetConfig()
	if err != nil {
		return err
	}

	activeProfile, err := c.GetActiveProfile()
	if err != nil {
		if err == config.ErrNoProfilesFound {
			activeProfile, err = createInitialProfile(c)
			if err != nil {
				return err
			}
		} else {
			return err
		}
	}

	if apiKey != nil {
		activeProfile.Api.Token = nil
		activeProfile.Api.Key = apiKey

		view_common.RenderInfoMessageBold("Successfully set Daytona API key!")
	}

	if tokenConfig != nil {
		activeProfile.Api.Key = nil
		activeProfile.Api.Token = tokenConfig

		err = c.EditProfile(activeProfile)
		if err != nil {
			return err
		}

		if activeProfile.Api.Key == nil {
			personalOrganizationId, err := common.GetPersonalOrganizationId(activeProfile)
			if err != nil {
				return err
			}

			activeProfile.ActiveOrganizationId = &personalOrganizationId
		}
	}

	return c.EditProfile(activeProfile)
}

func createInitialProfile(c *config.Config) (config.Profile, error) {
	profile := config.Profile{
		Id:   "initial",
		Name: "initial",
		Api: config.ServerApi{
			Url: defaultApiUrl(),
		},
	}

	return profile, c.AddProfile(profile)
}

func defaultApiUrl() string {
	if internal.Version == "v0.0.0-dev" {
		return "http://localhost:3001/api"
	}

	return config.GetDaytonaApiUrl()
}

// loginApiUrl is the API the login is for: the active profile's, or the one a new
// initial profile will get.
func loginApiUrl() string {
	c, err := config.GetConfig()
	if err != nil {
		return defaultApiUrl()
	}

	activeProfile, err := c.GetActiveProfile()
	if err != nil {
		return defaultApiUrl()
	}

	return activeProfile.Api.Url
}

// oidcConfig is the oidc block of /api/config: the login the API currently advertises.
type oidcConfig struct {
	Issuer   string `json:"issuer"`
	ClientId string `json:"clientId"`
	Provider string `json:"provider"`
}

const workosProvider = "workos"

// fetchOidcConfig decodes only the oidc block, so an API that predates a field of the
// full configuration schema does not break login. An API without `provider` is Auth0.
func fetchOidcConfig(ctx context.Context, apiUrl string) (oidcConfig, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimSuffix(apiUrl, "/")+"/config", nil)
	if err != nil {
		return oidcConfig{}, err
	}

	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return oidcConfig{}, fmt.Errorf("failed to fetch the login configuration: %w", err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK {
		return oidcConfig{}, fmt.Errorf("failed to fetch the login configuration: %s", response.Status)
	}

	var body struct {
		Oidc oidcConfig `json:"oidc"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		return oidcConfig{}, fmt.Errorf("failed to decode the login configuration: %w", err)
	}

	return body.Oidc, nil
}

func login(ctx context.Context) (*config.Token, error) {
	advertised, err := fetchOidcConfig(ctx, loginApiUrl())
	if err != nil {
		return nil, err
	}

	var token *config.Token
	if advertised.Provider == workosProvider {
		token, err = loginWithWorkOS(ctx, config.WorkOSClient{Issuer: advertised.Issuer, ClientId: advertised.ClientId})
	} else {
		token, err = loginWithAuth0(ctx)
	}
	if err != nil {
		return nil, err
	}

	view_common.RenderInfoMessageBold("Successfully logged in!")

	return token, nil
}

func loginWithWorkOS(ctx context.Context, client config.WorkOSClient) (*config.Token, error) {
	oauth2Config, err := auth.WorkOSConfig(client)
	if err != nil {
		return nil, err
	}

	// provider=authkit opens the hosted AuthKit page instead of a single provider.
	token, err := authorize(ctx, oauth2Config, oauth2.SetAuthURLParam("provider", "authkit"))
	if err != nil {
		return nil, err
	}

	return auth.NewToken(token, &client)
}

func loginWithAuth0(ctx context.Context) (*config.Token, error) {
	oauth2Config, provider, err := auth.Auth0Config(ctx)
	if err != nil {
		return nil, err
	}

	token, err := authorize(ctx, oauth2Config, oauth2.SetAuthURLParam("audience", config.GetAuth0Audience()))
	if err != nil {
		return nil, err
	}

	rawIDToken, ok := token.Extra("id_token").(string)
	if !ok {
		return nil, fmt.Errorf("no id_token in token response")
	}

	verifier := provider.Verifier(&oidc.Config{ClientID: oauth2Config.ClientID})
	_, err = verifier.Verify(ctx, rawIDToken)
	if err != nil {
		return nil, fmt.Errorf("failed to verify ID token: %w", err)
	}

	return auth.NewToken(token, nil)
}

// authorize runs the browser login: Authorization Code with PKCE (RFC 7636), which
// replaces a client secret for this public client, and a local callback server.
func authorize(ctx context.Context, oauth2Config oauth2.Config, authURLOptions ...oauth2.AuthCodeOption) (*oauth2.Token, error) {
	state, err := auth.GenerateRandomState()
	if err != nil {
		return nil, fmt.Errorf("failed to generate random state: %w", err)
	}

	pkceVerifier := oauth2.GenerateVerifier()

	authURL := oauth2Config.AuthCodeURL(state, append(authURLOptions, oauth2.S256ChallengeOption(pkceVerifier))...)

	view_common.RenderInfoMessageBold("Opening the browser for authentication ...")

	view_common.RenderInfoMessage("If opening fails, visit:\n")

	fmt.Println(authURL)

	_ = browser.OpenURL(authURL)

	code, err := auth.StartCallbackServer(state)
	if err != nil {
		return nil, fmt.Errorf("authentication failed: %w", err)
	}

	token, err := oauth2Config.Exchange(ctx, code, oauth2.VerifierOption(pkceVerifier))
	if err != nil {
		return nil, fmt.Errorf("failed to exchange token: %w", err)
	}

	return token, nil
}
