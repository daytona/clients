// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"iter"
	"net/http"
	"slices"
	"unicode/utf8"
)

// Secrets manages project secrets. Values are write-only and never retained.
// Obtain it from Client.Secrets.
type Secrets struct{ client *Client }

// Secrets returns the project's secret service without performing I/O.
func (c *Client) Secrets() *Secrets { return &Secrets{client: c} }

// ListSecretsOptions selects one page of secret metadata.
type ListSecretsOptions struct {
	// Cursor is the opaque continuation cursor.
	Cursor string
	// Limit is 1–200; zero uses the server default.
	Limit int
}

// SetSecretOptions controls replacement of a secret's value and metadata.
type SetSecretOptions struct {
	// AllowedDomains is a required nonempty list of HTTPS hosts (exact or
	// "*.example.com"). ["*"] permits any verified HTTPS host, including an
	// attacker-controlled host. Domain syntax is checked by the API.
	AllowedDomains []string
	// Description replaces the description; a pointer to "" clears it.
	// Nil preserves the stored description via a non-atomic Get before Set.
	Description *string
	// IfNotExists sends If-None-Match: * and skips the description read.
	// An existing secret returns ErrSecretAlreadyExists.
	IfNotExists bool
}

// List returns exactly one page of secret metadata, never secret values. It
// prefers eventually consistent search and falls back to the list route only on
// 404, SEARCH_NOT_CONFIGURED or a non-page answer from an older API.
func (s *Secrets) List(ctx context.Context, opts *ListSecretsOptions) (*Page[Secret], error) {
	options := ListSecretsOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	reader := resourceListReader{artifactListReader{s.client, "/secrets", &s.client.secretSearchFallback}, "secrets"}
	raw, err := reader.read(ctx, query)
	if err != nil {
		return nil, err
	}
	return resourcePage(raw, "secrets", parseSecret)
}

// ListAll lazily traverses secret metadata pages, stopping on cancellation,
// errors, a repeated cursor, or when the consumer breaks.
func (s *Secrets) ListAll(ctx context.Context, opts *ListSecretsOptions) iter.Seq2[Secret, error] {
	options := ListSecretsOptions{}
	if opts != nil {
		options = *opts
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[Secret], error) {
		pageOptions := options
		pageOptions.Cursor = cursor
		return s.List(ctx, &pageOptions)
	})
}

// Get fetches secret metadata by name. Values are never returned.
// ErrNotFound means the secret does not exist.
func (s *Secrets) Get(ctx context.Context, name string) (*Secret, error) {
	segment, err := resourceSegment(name, "secret")
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{Path: "/secrets/" + segment, Result: &raw}); requestErr != nil {
		return nil, requestErr
	}
	result, err := parseSecret(raw)
	if err != nil {
		return nil, err
	}
	return &result, nil
}

// Set creates or replaces a write-only secret and returns only metadata.
// A nonempty value and opts with nonempty AllowedDomains are required.
// Nil opts or invalid input returns ErrBadRequest before any request.
// Nil Description preserves stored metadata with one extra read (not atomic),
// unless IfNotExists is true.
// Required name and value remain positional to distinguish them from metadata.
func (s *Secrets) Set(ctx context.Context, name, value string, opts *SetSecretOptions) (*Secret, error) {
	segment, err := resourceSegment(name, "secret")
	if err != nil {
		return nil, err
	}
	if value == "" || !utf8.ValidString(value) {
		return nil, badRequest("value must be a non-empty UTF-8 string")
	}
	if opts == nil || len(opts.AllowedDomains) == 0 {
		return nil, badRequest("AllowedDomains must not be empty")
	}
	options := *opts
	domains := slices.Clone(options.AllowedDomains)
	if slices.ContainsFunc(domains, func(domain string) bool { return !utf8.ValidString(domain) }) {
		return nil, badRequest("AllowedDomains must contain UTF-8 strings")
	}
	description := options.Description
	if description != nil && !utf8.ValidString(*description) {
		return nil, badRequest("Description must be a UTF-8 string")
	}
	if description == nil && !options.IfNotExists {
		description, err = s.storedDescription(ctx, name)
		if err != nil {
			return nil, err
		}
	}
	body := struct {
		Value          string   `json:"value"`
		AllowedDomains []string `json:"allowed_domains"`
		Description    *string  `json:"description,omitempty"`
	}{value, domains, description}
	headers := make(http.Header)
	if options.IfNotExists {
		headers.Set("If-None-Match", "*")
	}
	var raw JSONValue
	if requestErr := s.client.api().do(ctx, apiRequest{
		Method: http.MethodPut, Path: "/secrets/" + segment, Body: body, Headers: headers, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	result, err := parseSecret(raw)
	if err != nil {
		return nil, err
	}
	return &result, nil
}

// A replacement preserves description unless the caller explicitly clears it.
// Not-found alone permits a new secret; permission and parsing failures must
// abort before the write. Keep that read-before-replace policy separate from
// the request encoding.
func (s *Secrets) storedDescription(ctx context.Context, name string) (*string, error) {
	stored, err := s.Get(ctx, name)
	switch {
	case errors.Is(err, ErrNotFound):
		return nil, nil //nolint:nilnil // A missing description is a successful, absent optional metadata value.
	case err != nil:
		return nil, err
	default:
		return stored.Description, nil
	}
}

// Delete deletes a secret by name. ErrNotFound is preserved.
func (s *Secrets) Delete(ctx context.Context, name string) error {
	segment, err := resourceSegment(name, "secret")
	if err != nil {
		return err
	}
	return s.client.api().do(ctx, apiRequest{Method: http.MethodDelete, Path: "/secrets/" + segment})
}

// String returns a credential-free diagnostic name.
func (s *Secrets) String() string { return "daytona.Secrets" }

// GoString returns a credential-free diagnostic name.
func (s *Secrets) GoString() string { return s.String() }
