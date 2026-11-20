// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"log/slog"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// Client is a concurrency-safe Daytona client. Construct it with New and close
// it when finished. Its zero value is not usable.
type Client struct {
	config         clientConfig
	http           *http.Client
	ownsTransport  bool
	ctx            context.Context
	cancel         context.CancelCauseFunc
	closeOnce      sync.Once
	workers        sync.WaitGroup
	workerMu       sync.Mutex
	apiClient      *apiClient
	auth           credentialProvider
	stateSource    sandboxStateSource
	searchFallback atomic.Bool
	version        string
}

// New resolves options and environment without making a network request.
// Exactly one API key or JWT is required. JWT authentication also requires a
// project ID. Invalid configuration returns ErrBadRequest; missing credentials
// return ErrAuthentication.
func New(opts ...Option) (*Client, error) {
	config := clientConfig{
		timeout: defaultRequestTimeout, maxResponseBytes: defaultMaxResponseBytes, logger: slog.Default(),
	}
	for _, option := range opts {
		if option == nil {
			return nil, badRequest("option must not be nil")
		}
		if err := option(&config); err != nil {
			return nil, err
		}
	}
	if err := config.resolveEnvironment(); err != nil {
		return nil, err
	}
	warnLegacyURL(config.logger)
	ctx, cancel := context.WithCancelCause(context.Background())
	client := &Client{
		config: config, ctx: ctx, cancel: cancel, version: sdkVersion(), stateSource: noopStateSource{},
	}
	client.http, client.ownsTransport = sdkHTTPClient(config.httpClient)
	if config.apiKey != nil {
		client.auth = staticCredential(*config.apiKey)
	} else {
		client.auth = &projectCredential{cache: tokenCache{owner: client, now: time.Now}, owner: client}
	}
	client.apiClient = &apiClient{transport: transport{owner: client, baseURL: config.baseURL, auth: client.auth}}
	client.installSandboxEvents()
	return client, nil
}

// Close cancels outstanding work and releases SDK-owned idle connections.
// It is idempotent. New calls through this client or its handles return
// ErrClientClosed. Injected transports remain owned by the caller.
func (c *Client) Close() error {
	c.closeOnce.Do(func() {
		c.workerMu.Lock()
		c.cancel(closedError())
		c.workerMu.Unlock()
		c.closeSandboxEvents()
		c.workers.Wait()
		if c.ownsTransport {
			c.http.CloseIdleConnections()
		}
	})
	return nil
}

func (c *Client) ensureOpen() error {
	if c.ctx.Err() != nil {
		return closedError()
	}
	return nil
}

func (c *Client) api() *apiClient { return c.apiClient }

func sdkHTTPClient(injected *http.Client) (*http.Client, bool) {
	client := &http.Client{}
	owned := injected == nil
	if injected != nil {
		*client = *injected
	}
	if client.Transport == nil {
		client.Transport = &http.Transport{
			Proxy:             http.ProxyFromEnvironment,
			DialContext:       (&net.Dialer{KeepAlive: 30 * time.Second}).DialContext,
			ForceAttemptHTTP2: true, MaxIdleConns: 100, MaxIdleConnsPerHost: 100,
			IdleConnTimeout: 90 * time.Second, ExpectContinueTimeout: time.Second,
		}
		owned = true
	}
	client.Timeout = 0
	client.Jar = nil
	client.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	return client, owned
}

// String returns a credential-free diagnostic name.
func (c *Client) String() string { return "daytona.Client" }

// GoString returns a credential-free diagnostic name.
func (c *Client) GoString() string { return c.String() }
