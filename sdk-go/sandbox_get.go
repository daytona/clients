// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/hex"
	"net/url"
	"strings"
	"unicode/utf8"
)

// GetSandboxOptions configures a sandbox connection. ToolboxURL is an explicitly
// trusted direct URL; {sandboxId} and {sandbox_id} path placeholders are expanded.
// AccessToken is caller-owned and is never automatically replaced.
type GetSandboxOptions struct {
	// ToolboxURL overrides the sandbox's tools destination with a trusted HTTP
	// or HTTPS URL, without a query or fragment.
	ToolboxURL string
	// AccessToken is an optional caller-managed sandbox token.
	AccessToken string
}

// Get fetches a sandbox by ID. It validates input before making a request and
// returns ErrNotFound for a missing sandbox.
func (c *Client) Get(ctx context.Context, id string, opts *GetSandboxOptions) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	options := GetSandboxOptions{}
	if opts != nil {
		options = *opts
	}
	if options.ToolboxURL != "" && strings.TrimSpace(options.ToolboxURL) == "" {
		return nil, badRequest("toolboxUrl must be a non-empty string")
	}
	if options.AccessToken != "" {
		if err := headerText(options.AccessToken, "accessToken"); err != nil {
			return nil, err
		}
	}
	if _, err := scopedToolboxURL("", id, options.ToolboxURL); err != nil {
		return nil, err
	}
	observation, err := c.getSandbox(ctx, id)
	if err != nil {
		return nil, err
	}
	return c.newSandbox(sandboxInit{Info: observation.info, ToolboxURL: observation.toolboxURL, Options: options})
}

func (c *Client) getSandbox(ctx context.Context, id string) (sandboxObservation, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return sandboxObservation{}, err
	}
	var raw JSONValue
	if requestErr := c.api().do(ctx, apiRequest{Path: "/sandboxes/" + segment, Result: &raw}); requestErr != nil {
		return sandboxObservation{}, requestErr
	}
	return parseSandbox(raw)
}

func pathSegment(value string) (string, error) {
	if strings.TrimSpace(value) == "" || value == "." || value == ".." ||
		!utf8.ValidString(value) {
		return "", badRequest("path identifier must be non-blank, valid UTF-8, and not . or ..")
	}
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20"), nil
}

func checkResponseID(value string) error {
	// encoding/json replaces lone surrogates with U+FFFD. Response IDs must
	// reject that lossy representation; literal caller input remains valid.
	if strings.ContainsRune(value, utf8.RuneError) {
		return badRequest("response path identifier must not contain a replacement character")
	}
	_, err := pathSegment(value)
	return err
}

func headerText(value, name string) error {
	if value == "" {
		return badRequest(name + " must be a non-empty string")
	}
	for _, char := range value {
		if char < 32 || char > 126 {
			return badRequest(name + " must be printable ASCII with no leading or trailing whitespace")
		}
	}
	if strings.HasPrefix(value, " ") || strings.HasSuffix(value, " ") {
		return badRequest(name + " must be printable ASCII with no leading or trailing whitespace")
	}
	return nil
}

func regionOf(id string) string {
	// Bare UUIDs contain delimiters but no region tag.
	if len(id) == 36 && id[8] == '-' && id[13] == '-' && id[18] == '-' && id[23] == '-' {
		if _, err := hex.DecodeString(strings.ReplaceAll(id, "-", "")); err == nil {
			return ""
		}
	}
	first, region, found := strings.Cut(id, "-")
	if !found || first == "" || region == "" {
		return ""
	}
	return region
}
