// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func secretFixture() map[string]any {
	return map[string]any{
		"name": "TOKEN", "allowed_domains": []string{"api.example.com"},
		"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
	}
}

func TestSecretsSet_whenDescriptionIsOmittedOrReplaced(t *testing.T) {
	kept, replacement, empty := "kept", "replacement", ""
	for _, tt := range []struct {
		name    string
		stored  *string
		status  int
		opts    SetSecretOptions
		methods []string
		body    string
	}{
		{"preserve", &kept, 200, SetSecretOptions{}, []string{"GET", "PUT"}, `{"value":"value","allowed_domains":["*"],"description":"kept"}`},
		{"null stored", nil, 200, SetSecretOptions{}, []string{"GET", "PUT"}, `{"value":"value","allowed_domains":["*"]}`},
		{"create", nil, 404, SetSecretOptions{}, []string{"GET", "PUT"}, `{"value":"value","allowed_domains":["*"]}`},
		{"clear", &kept, 200, SetSecretOptions{Description: &empty}, []string{"PUT"}, `{"value":"value","allowed_domains":["*"],"description":""}`},
		{"replace", &kept, 200, SetSecretOptions{Description: &replacement}, []string{"PUT"}, `{"value":"value","allowed_domains":["*"],"description":"replacement"}`},
		{"guard", &kept, 200, SetSecretOptions{IfNotExists: true}, []string{"PUT"}, `{"value":"value","allowed_domains":["*"]}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fixture := secretFixture()
			fixture["description"] = tt.stored
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /secrets/{name}": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tt.status)
					writeJSON(t, w, fixture)
				},
				"PUT /secrets/{name}": jsonHandler(t, fixture),
			})
			client := newTestClient(t, fake.Server)
			opts := tt.opts
			opts.AllowedDomains = []string{"*"}
			// When
			got, err := client.Secrets().Set(t.Context(), "API/TOKEN", "value", &opts)
			// Then
			require.NoError(t, err)
			require.Equal(t, "TOKEN", got.Name)
			requests := fake.requests()
			methods := make([]string, 0, len(requests))
			for _, request := range requests {
				methods = append(methods, request.Method)
				require.Equal(t, "/secrets/API%2FTOKEN", request.Path)
			}
			require.Equal(t, tt.methods, methods)
			last := requests[len(requests)-1]
			require.JSONEq(t, tt.body, string(last.Body))
			if opts.IfNotExists {
				require.Equal(t, "*", last.Header.Get("If-None-Match"))
			} else {
				require.Empty(t, last.Header.Get("If-None-Match"))
			}
		})
	}
}

func TestSecretsSet_whenDescriptionReadFails(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"GET /secrets/TOKEN": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusForbidden)
			writeJSON(t, w, map[string]string{"code": "FORBIDDEN", "message": "denied"})
		},
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Secrets().Set(t.Context(), "TOKEN", "value", &SetSecretOptions{AllowedDomains: []string{"*"}})
	// Then
	require.ErrorIs(t, err, ErrForbidden)
	require.Len(t, fake.requests(), 1)
}

func TestSecretsSet_whenCreationGuardFails(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PUT /secrets/TOKEN": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusPreconditionFailed)
			writeJSON(t, w, map[string]string{"code": "SECRET_ALREADY_EXISTS", "source": "API", "message": "exists"})
		},
	})
	client := newTestClient(t, fake.Server)
	// When
	_, err := client.Secrets().Set(t.Context(), "TOKEN", "value", &SetSecretOptions{
		AllowedDomains: []string{"*"}, IfNotExists: true,
	})
	// Then
	require.ErrorIs(t, err, ErrSecretAlreadyExists)
	require.Len(t, fake.requests(), 1)
}

func TestSecretsMetadata_whenServerIncludesWriteOnlyValue(t *testing.T) {
	for _, method := range []string{"get", "set", "list"} {
		t.Run(method, func(t *testing.T) {
			// Given
			fixture := secretFixture()
			fixture["value"], fixture["future_value"] = "never-return-this-value", "never-return-this-value"
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"GET /secrets/TOKEN": jsonHandler(t, fixture),
				"PUT /secrets/TOKEN": jsonHandler(t, fixture),
				"GET /secrets":       jsonHandler(t, map[string]any{"secrets": []any{fixture}}),
			})
			client := newTestClient(t, fake.Server)
			var got *Secret
			var err error
			// When
			switch method {
			case "get":
				got, err = client.Secrets().Get(t.Context(), "TOKEN")
			case "set":
				got, err = client.Secrets().Set(t.Context(), "TOKEN", "never-return-this-value", &SetSecretOptions{
					AllowedDomains: []string{"api.example.com"}, IfNotExists: true,
				})
			case "list":
				var page *Page[Secret]
				page, err = client.Secrets().List(t.Context(), nil)
				if err == nil {
					require.Len(t, page.Items, 1)
					got = &page.Items[0]
				}
			}
			// Then
			require.NoError(t, err)
			require.Equal(t, "TOKEN", got.Name)
			require.Equal(t, []string{"api.example.com"}, got.AllowedDomains)
			require.Nil(t, got.Description)
			require.NotContains(t, fmt.Sprintf("%v %+v %#v", got, got, got), "never-return-this-value")
			require.NotContains(t, fmt.Sprintf("%v %#v", client.Secrets(), client.Secrets()), "never-return-this-value")
		})
	}
}

func TestSecretsDelete_whenNameContainsSlash(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"DELETE /secrets/{name}": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	client := newTestClient(t, fake.Server)
	// When
	err := client.Secrets().Delete(t.Context(), "API/TOKEN")
	// Then
	require.NoError(t, err)
	require.Equal(t, "/secrets/API%2FTOKEN", fake.requests()[0].Path)
}

func TestSecretsRejectInput_whenInvalid(t *testing.T) {
	badText := string([]byte{255})
	for _, tt := range []struct {
		name string
		call func(context.Context, *Secrets) error
	}{
		{"get dot", func(ctx context.Context, s *Secrets) error { _, err := s.Get(ctx, ".."); return err }},
		{"delete blank", func(ctx context.Context, s *Secrets) error { return s.Delete(ctx, " ") }},
		{"set dot", func(ctx context.Context, s *Secrets) error { _, err := s.Set(ctx, ".", "v", nil); return err }},
		{"empty value", func(ctx context.Context, s *Secrets) error {
			_, err := s.Set(ctx, "S", "", &SetSecretOptions{AllowedDomains: []string{"*"}})
			return err
		}},
		{"invalid value", func(ctx context.Context, s *Secrets) error {
			_, err := s.Set(ctx, "S", badText, &SetSecretOptions{AllowedDomains: []string{"*"}})
			return err
		}},
		{"nil options", func(ctx context.Context, s *Secrets) error { _, err := s.Set(ctx, "S", "v", nil); return err }},
		{"nil domains", func(ctx context.Context, s *Secrets) error {
			_, err := s.Set(ctx, "S", "v", &SetSecretOptions{})
			return err
		}},
		{"empty domains", func(ctx context.Context, s *Secrets) error {
			_, err := s.Set(ctx, "S", "v", &SetSecretOptions{AllowedDomains: []string{}})
			return err
		}},
		{"invalid domains", func(ctx context.Context, s *Secrets) error {
			_, err := s.Set(ctx, "S", "v", &SetSecretOptions{AllowedDomains: []string{badText}})
			return err
		}},
		{"invalid description", func(ctx context.Context, s *Secrets) error {
			_, err := s.Set(ctx, "S", "v", &SetSecretOptions{AllowedDomains: []string{"*"}, Description: &badText})
			return err
		}},
		{"limit", func(ctx context.Context, s *Secrets) error {
			_, err := s.List(ctx, &ListSecretsOptions{Limit: 201})
			return err
		}},
		{"cursor", func(ctx context.Context, s *Secrets) error {
			_, err := s.List(ctx, &ListSecretsOptions{Cursor: badText})
			return err
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			err := tt.call(t.Context(), client.Secrets())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
