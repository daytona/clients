// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"encoding/base64"
	"fmt"
	"testing"
	"time"

	"github.com/daytona/clients/cli/config"
	"golang.org/x/oauth2"
)

func jwtWithPayload(payload string) string {
	return "header." + base64.RawURLEncoding.EncodeToString([]byte(payload)) + ".signature"
}

func TestWorkOSConfigUsesTheIssuerHost(t *testing.T) {
	oauth2Config, err := WorkOSConfig(config.WorkOSClient{
		Issuer:   "https://auth.example.com/user_management/client_123",
		ClientId: "client_123",
	})
	if err != nil {
		t.Fatal(err)
	}

	if oauth2Config.Endpoint.AuthURL != "https://auth.example.com/user_management/authorize" {
		t.Errorf("AuthURL = %s", oauth2Config.Endpoint.AuthURL)
	}
	if oauth2Config.Endpoint.TokenURL != "https://auth.example.com/user_management/authenticate" {
		t.Errorf("TokenURL = %s", oauth2Config.Endpoint.TokenURL)
	}
	if oauth2Config.ClientID != "client_123" {
		t.Errorf("ClientID = %s", oauth2Config.ClientID)
	}
	if oauth2Config.Endpoint.AuthStyle != oauth2.AuthStyleInParams {
		t.Error("public client must send client_id in the request body")
	}
}

func TestWorkOSConfigRejectsAnIssuerWithoutHost(t *testing.T) {
	if _, err := WorkOSConfig(config.WorkOSClient{Issuer: "user_management/client_123"}); err == nil {
		t.Error("expected an error for an issuer without scheme and host")
	}
}

func TestNewTokenReadsExpiryFromTheAccessTokenWhenTheResponseHasNone(t *testing.T) {
	exp := time.Now().Add(15 * time.Minute).Truncate(time.Second)
	workos := &config.WorkOSClient{Issuer: "https://auth.example.com", ClientId: "client_123"}

	token, err := NewToken(&oauth2.Token{
		AccessToken:  jwtWithPayload(fmt.Sprintf(`{"exp":%d}`, exp.Unix())),
		RefreshToken: "refresh",
	}, workos)
	if err != nil {
		t.Fatal(err)
	}

	if !token.ExpiresAt.Equal(exp) {
		t.Errorf("ExpiresAt = %s, want %s", token.ExpiresAt, exp)
	}
	if token.WorkOS != workos || token.RefreshToken != "refresh" {
		t.Error("token must keep its refresh token and issuing provider")
	}
}

func TestNewTokenKeepsTheResponseExpiry(t *testing.T) {
	expiry := time.Now().Add(time.Hour)

	token, err := NewToken(&oauth2.Token{AccessToken: "opaque", Expiry: expiry}, nil)
	if err != nil {
		t.Fatal(err)
	}

	if !token.ExpiresAt.Equal(expiry) || token.WorkOS != nil {
		t.Errorf("unexpected token %+v", token)
	}
}

func TestNewTokenFailsWithoutAnyExpiry(t *testing.T) {
	for _, accessToken := range []string{"opaque", jwtWithPayload(`{}`), "a.!!!.c"} {
		if _, err := NewToken(&oauth2.Token{AccessToken: accessToken}, nil); err == nil {
			t.Errorf("expected an error for access token %q", accessToken)
		}
	}
}
