// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"testing/synctest"
	"time"

	"github.com/stretchr/testify/require"
)

func TestGitRemote_whenCredentialsAreProvided(t *testing.T) {
	for _, test := range []struct {
		name, path, body string
		call             func(context.Context, *Git) error
	}{
		{
			"clone", "clone", `{"url":"https://example.org/repo","path":"/repo","branch":"dev","commit_id":"sha","depth":2,"insecureSkipTls":true,"username":"user","password":"pass"}`,
			func(ctx context.Context, g *Git) error {
				return g.Clone(ctx, "https://example.org/repo", "/repo", &GitCloneOptions{
					Branch: "dev", CommitID: "sha", Depth: 2, InsecureSkipTLS: true, Username: "user", Password: "pass",
				})
			},
		},
		{
			"clone defaults", "clone", `{"url":"url","path":"repo"}`,
			func(ctx context.Context, g *Git) error { return g.Clone(ctx, "url", "repo", nil) },
		},
		{
			"pull", "pull", `{"path":"/repo","username":"user","password":"pass","branch":"+dev","remote":"upstream"}`,
			func(ctx context.Context, g *Git) error {
				return g.Pull(ctx, "/repo", &GitCredentialOptions{Username: "user", Password: "pass", Branch: "+dev", Remote: "upstream"})
			},
		},
		{
			"pull defaults", "pull", `{"path":"/repo"}`,
			func(ctx context.Context, g *Git) error { return g.Pull(ctx, "/repo", nil) },
		},
		{
			"push", "push", `{"path":"/repo","username":"git","password":"pat","branch":"dev","remote":"upstream","setUpstream":true}`,
			func(ctx context.Context, g *Git) error {
				return g.Push(ctx, "/repo", &GitPushOptions{
					GitCredentialOptions: GitCredentialOptions{Token: "pat", Branch: "dev", Remote: "upstream"}, SetUpstream: true,
				})
			},
		},
		{
			"push defaults", "push", `{"path":"/repo"}`,
			func(ctx context.Context, g *Git) error { return g.Push(ctx, "/repo", nil) },
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			git, fake := gitTestService(t, map[string]http.HandlerFunc{
				"POST /toolbox/sbx/git/" + test.path: jsonHandler(t, struct{}{}),
			})
			// When
			err := test.call(t.Context(), git)
			// Then
			require.NoError(t, err)
			require.JSONEq(t, test.body, string(fake.requests()[0].Body))
		})
	}
}

func TestGitRemote_whenDeadlineReplacesClientBudget(t *testing.T) {
	// Given: a real wire response that starts then never finishes its JSON body.
	started := make(chan struct{})
	git, _ := gitTestService(t, map[string]http.HandlerFunc{
		"POST /toolbox/sbx/git/pull": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			if flushErr := http.NewResponseController(w).Flush(); flushErr != nil {
				t.Errorf("flush response: %v", flushErr)
				return
			}
			close(started)
			<-r.Context().Done()
		},
	})
	// When
	err := git.Pull(t.Context(), "/repo", &GitCredentialOptions{Timeout: 20 * time.Millisecond})
	// Then: a total deadline covers the body, not merely opening the response.
	<-started
	require.ErrorIs(t, err, ErrClientTimeout)
	require.ErrorIs(t, err, context.DeadlineExceeded)
}

func TestGitRemote_whenCallBudgetExceedsClientTimeout(t *testing.T) {
	for _, test := range []struct {
		name    string
		timeout time.Duration
		delay   time.Duration
		want    error
	}{
		{"longer override", time.Second, 100 * time.Millisecond, nil},
		{"default thirty minutes", 0, 31 * time.Minute, ErrClientTimeout},
		{"explicit unbounded", NoTimeout, time.Hour, nil},
		{"above timer maximum", maxTimerDuration + time.Second, time.Hour, nil},
		{"positive submillisecond rounds upward", time.Nanosecond, 500 * time.Microsecond, nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given: the transport observes the request context, not SDK internals.
				client, err := New(WithAPIKey("fixture"), WithRequestTimeout(time.Millisecond),
					WithHTTPClient(&http.Client{Transport: gitDeadlineTransport{delay: test.delay}}))
				require.NoError(t, err)
				t.Cleanup(func() { require.NoError(t, client.Close()) })
				sandbox, err := client.newSandbox(sandboxInit{
					Info: SandboxInfo{ID: "sbx"}, ToolboxURL: "https://example.org",
					Options: GetOptions{AccessToken: "caller"},
				})
				require.NoError(t, err)
				// When
				err = sandbox.Git().Push(t.Context(), "/repo", &GitPushOptions{
					GitCredentialOptions: GitCredentialOptions{Timeout: test.timeout},
				})
				// Then
				if test.want != nil {
					require.ErrorIs(t, err, test.want)
				} else {
					require.NoError(t, err)
				}
			})
		})
	}
}

type gitDeadlineTransport struct{ delay time.Duration }

func (r gitDeadlineTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	timer := time.NewTimer(r.delay)
	defer timer.Stop()
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	case <-timer.C:
		return &http.Response{StatusCode: http.StatusNoContent, Header: make(http.Header), Body: http.NoBody}, nil
	}
}
