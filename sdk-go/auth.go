// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"sync"
	"time"
)

type credentialProvider interface {
	bearer(context.Context) (string, error)
	unauthorized(context.Context, string) (string, error)
}

type staticCredential string

func (s staticCredential) bearer(ctx context.Context) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", transportError(ctx, err)
	}
	return string(s), nil
}
func (s staticCredential) unauthorized(context.Context, string) (string, error) { return "", nil }

type cachedToken struct {
	token     string
	expiresAt time.Time
	refreshAt time.Time
}

type tokenFlight struct {
	done  chan struct{}
	value cachedToken
	err   error
}

type tokenCache struct {
	owner  *Client
	now    func() time.Time
	mu     sync.Mutex
	value  cachedToken
	flight *tokenFlight
}

func (c *tokenCache) snapshot() cachedToken {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.value
}

func (c *tokenCache) fresh(token cachedToken) bool {
	return token.token != "" && (token.refreshAt.IsZero() || c.now().Before(token.refreshAt))
}

// resolve keeps a shared mint alive when one waiter cancels. Client.Close and
// the client's request budget bound the mint itself.
func (c *tokenCache) resolve(ctx context.Context, rejected string,
	load func(context.Context) (cachedToken, error),
) (string, error) {
	c.mu.Lock()
	if c.fresh(c.value) && (rejected == "" || c.value.token != rejected) {
		token := c.value.token
		c.mu.Unlock()
		return token, nil
	}
	flight := c.flight
	if flight == nil {
		flight = &tokenFlight{done: make(chan struct{})}
		c.owner.workerMu.Lock()
		if err := c.owner.ensureOpen(); err != nil {
			c.owner.workerMu.Unlock()
			c.mu.Unlock()
			return "", err
		}
		c.owner.workers.Add(1)
		c.flight = flight
		go c.run(load, flight)
		c.owner.workerMu.Unlock()
	}
	c.mu.Unlock()
	select {
	case <-ctx.Done():
		return "", transportError(ctx, ctx.Err())
	case <-flight.done:
		return flight.value.token, flight.err
	}
}

func (c *tokenCache) run(load func(context.Context) (cachedToken, error), flight *tokenFlight) {
	defer c.owner.workers.Done()
	value, err := load(c.owner.ctx)
	c.mu.Lock()
	defer c.mu.Unlock()
	flight.value, flight.err = value, err
	if err == nil {
		c.value = value
	}
	c.flight = nil
	close(flight.done)
}

type projectCredential struct {
	cache tokenCache
	owner *Client
}

func (p *projectCredential) bearer(ctx context.Context) (string, error) {
	return p.cache.resolve(ctx, "", p.exchange)
}

func (p *projectCredential) unauthorized(ctx context.Context, rejected string) (string, error) {
	return p.cache.resolve(ctx, rejected, p.exchange)
}

func (p *projectCredential) exchange(ctx context.Context) (cachedToken, error) {
	config := p.owner.config
	transport := transport{owner: p.owner, baseURL: config.baseURL, auth: staticCredential(*config.jwtToken)}
	//nolint:gosec // OAuth grant/type URNs are public protocol identifiers; the JWT comes from client configuration.
	body := struct {
		GrantType        string `json:"grant_type"`
		SubjectToken     string `json:"subject_token"`
		SubjectTokenType string `json:"subject_token_type"`
		ProjectID        string `json:"project_id"`
	}{
		GrantType:    "urn:ietf:params:oauth:grant-type:token-exchange",
		SubjectToken: *config.jwtToken, SubjectTokenType: "urn:ietf:params:oauth:token-type:jwt",
		ProjectID: *config.projectID,
	}
	var raw JSONValue
	if err := transport.do(ctx, apiRequest{Method: "POST", Path: "/auth/token", Body: body, Result: &raw}); err != nil {
		return cachedToken{}, err
	}
	object, err := parseObject(raw, "token exchange")
	if err != nil {
		return cachedToken{}, err
	}
	token, err := field[string](object, "access_token", true)
	if err != nil || token == "" {
		return cachedToken{}, badRequest("token exchange response access_token must be a non-empty string")
	}
	seconds, err := integerField(object, "expires_in", true)
	if err != nil || seconds <= 0 || seconds > maxSafeInteger {
		return cachedToken{}, badRequest("token exchange response expires_in must be a positive integer")
	}
	ttl := saturatedDuration(seconds, time.Second)
	now := p.cache.now()
	expiry := now.Add(ttl)
	return cachedToken{token: token, expiresAt: expiry, refreshAt: expiry.Add(-min(30*time.Second, ttl/2))}, nil
}
