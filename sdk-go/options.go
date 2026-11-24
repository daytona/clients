// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"log/slog"
	"net/http"
	"time"
)

// Option configures a Client. Construct options with the With functions.
type Option func(*clientConfig) error

type clientConfig struct {
	apiKey, jwtToken, projectID *string
	baseURL                     string
	timeout                     time.Duration
	maxResponseBytes            int64
	httpClient                  *http.Client
	logger                      *slog.Logger
	events                      SandboxEventsConfig
}

// SandboxEventsConfig configures sandbox state subscriptions. A nil Enabled
// defaults to enabled; DAYTONA_SANDBOX_EVENTS=0 disables subscriptions.
// The events hub is independent of ordinary request timeouts.
type SandboxEventsConfig struct {
	// Enabled defaults to true when nil.
	Enabled *bool
	// ReconcileInterval is the hub reconciliation interval; zero uses its default.
	ReconcileInterval time.Duration
	// IdleClose is the idle connection lifetime; zero or NoTimeout never
	// idle-closes a connection. Client.Close still closes it.
	IdleClose time.Duration
}

// WithAPIKey selects API-key authentication, suppressing an ambient JWT.
// An empty value, or one that is not printable ASCII without leading or
// trailing whitespace, returns ErrBadRequest.
func WithAPIKey(key string) Option {
	return func(c *clientConfig) error {
		c.apiKey = &key
		return nil
	}
}

// WithJWTToken selects JWT authentication, suppressing an ambient API key.
// WithProjectID or DAYTONA_PROJECT_ID must also select a project.
func WithJWTToken(token string) Option {
	return func(c *clientConfig) error {
		c.jwtToken = &token
		return nil
	}
}

// WithProjectID selects the project for JWT token exchange. Empty is invalid.
func WithProjectID(id string) Option {
	return func(c *clientConfig) error {
		c.projectID = &id
		return nil
	}
}

// WithRequestTimeout sets the total ordinary-request budget (default 60 seconds).
// Zero keeps the default; NoTimeout or values above 2,147,483.647 seconds disable
// it. Transfers use this duration as an idle timeout instead.
func WithRequestTimeout(timeout time.Duration) Option {
	return func(c *clientConfig) error {
		if err := checkDuration("WithRequestTimeout", timeout, NoTimeout); err != nil {
			return err
		}
		if timeout != 0 {
			c.timeout = boundedTimeout(timeout)
		}
		return nil
	}
}

// WithMaxResponseBytes caps buffered decoded response bodies (default 1 GiB).
// Zero keeps the default; NoLimit disables the cap. Streamed bodies are not
// buffered or capped as a whole, but individual SSE events use this limit.
func WithMaxResponseBytes(limit int64) Option {
	return func(c *clientConfig) error {
		if limit < NoLimit || limit > maxSafeInteger {
			return badRequest("WithMaxResponseBytes must be between 0 and 9007199254740991, or NoLimit")
		}
		if limit != 0 {
			c.maxResponseBytes = limit
		}
		return nil
	}
}

// WithHTTPClient uses a shallow copy of client, preserving its RoundTripper.
// The SDK disables its cookie jar, redirects, and total Timeout, replacing that
// timeout with WithRequestTimeout. The caller retains ownership of its transport.
func WithHTTPClient(client *http.Client) Option {
	return func(c *clientConfig) error {
		if client == nil {
			return badRequest("WithHTTPClient requires a non-nil client")
		}
		c.httpClient = client
		return nil
	}
}

// WithLogger selects the logger for SDK diagnostics (default slog.Default).
func WithLogger(logger *slog.Logger) Option {
	return func(c *clientConfig) error {
		if logger == nil {
			return badRequest("WithLogger requires a non-nil logger")
		}
		c.logger = logger
		return nil
	}
}

// WithSandboxEvents configures state subscriptions. Configuration is copied.
func WithSandboxEvents(events SandboxEventsConfig) Option {
	return func(c *clientConfig) error {
		if err := checkDuration("SandboxEventsConfig.ReconcileInterval", events.ReconcileInterval); err != nil {
			return err
		}
		if err := checkDuration("SandboxEventsConfig.IdleClose", events.IdleClose, NoTimeout); err != nil {
			return err
		}
		c.events = events
		if events.Enabled != nil {
			enabled := *events.Enabled
			c.events.Enabled = &enabled
		}
		return nil
	}
}
