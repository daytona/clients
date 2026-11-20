// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"log/slog"
	"net/url"
	"os"
	"strings"
	"sync"
)

// DefaultAPIURL is the Daytona Cloud endpoint. Only DAYTONA_V2_API_URL overrides it.
const DefaultAPIURL = "https://api.daytona.com"

var legacyURLWarning sync.Once

func (c *clientConfig) resolveEnvironment() error {
	for name, value := range map[string]*string{
		"apiKey": c.apiKey, "jwtToken": c.jwtToken, "projectId": c.projectID,
	} {
		if value != nil && *value == "" {
			return badRequest(name + " must be a non-empty string when provided")
		}
	}
	explicitKey, explicitJWT := c.apiKey != nil, c.jwtToken != nil
	if !explicitJWT && !explicitKey {
		c.apiKey = nonBlankEnv("DAYTONA_API_KEY")
		c.jwtToken = nonBlankEnv("DAYTONA_JWT_TOKEN")
	}
	if c.projectID == nil {
		c.projectID = nonBlankEnv("DAYTONA_PROJECT_ID")
	}
	switch {
	case c.apiKey == nil && c.jwtToken == nil:
		return newSDKError("UNAUTHORIZED", 401,
			"configure apiKey or jwtToken (set config.* or DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)")
	case c.apiKey != nil && c.jwtToken != nil:
		return badRequest(
			"configure exactly one of apiKey or jwtToken (set config.* or DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)")
	case c.jwtToken != nil && c.projectID == nil:
		return badRequest("projectId is required when jwtToken is set (set config.projectId or DAYTONA_PROJECT_ID)")
	}
	c.baseURL = strings.TrimRight(strings.TrimSpace(os.Getenv("DAYTONA_V2_API_URL")), "/")
	if c.baseURL == "" {
		c.baseURL = DefaultAPIURL
	}
	_, err := parseHTTPBase(c.baseURL)
	return err
}

func nonBlankEnv(name string) *string {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return nil
	}
	return &value
}

func warnLegacyURL(logger *slog.Logger) {
	value := strings.TrimRight(strings.TrimSpace(os.Getenv("DAYTONA_API_URL")), "/")
	if value == "" {
		return
	}
	if parsed, err := url.Parse(value); err == nil {
		parsed.Scheme = strings.ToLower(parsed.Scheme)
		parsed.Host = strings.ToLower(parsed.Host)
		value = parsed.String()
	}
	if value == DefaultAPIURL || value == "https://app.daytona.io/api" {
		return
	}
	legacyURLWarning.Do(func() {
		logger.Warn("DAYTONA_API_URL is set, but SDK 2.x ignores it and connects to Daytona Cloud " +
			"(DAYTONA_V2_API_URL selects the v2 endpoint). For a custom API URL, stay on SDK 0.x.")
	})
}
