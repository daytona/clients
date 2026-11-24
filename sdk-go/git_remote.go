// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"math"
	"net/http"
	"time"

	wire "go.daytona.com/sdk/v2/internal/gen/toolbox"
)

// GitCloneOptions configures a clone. Nil options uses a full clone and a
// 30-minute request deadline, independently of the client's request timeout.
type GitCloneOptions struct {
	// Branch selects a branch when CommitID is absent.
	Branch string
	// CommitID detaches the clone at this commit.
	CommitID string
	// Depth selects a positive shallow depth; zero omits it.
	Depth int
	// InsecureSkipTLS disables certificate checks only for this clone.
	InsecureSkipTLS bool
	// Username authenticates to the remote.
	Username string
	// Password authenticates to the remote. For a personal access token, set
	// Username to "git" and Password to the token.
	Password string
	// Timeout replaces the total request deadline; zero defaults to 30 minutes,
	// NoTimeout disables it. The caller context always bounds the request.
	Timeout time.Duration
}

// GitCredentialOptions selects a remote and its authentication.
type GitCredentialOptions struct {
	// Username authenticates to the remote.
	Username string
	// Password authenticates to the remote. For a personal access token, set
	// Username to "git" and Password to the token.
	Password string
	// Branch selects a remote branch; empty preserves Git's default behavior.
	Branch string
	// Remote selects a named remote; empty preserves Git's default behavior.
	Remote string
	// Timeout replaces the total request deadline; zero defaults to 30 minutes,
	// NoTimeout disables it. Context cancellation remains effective.
	Timeout time.Duration
}

// GitPushOptions configures push credentials and upstream tracking.
type GitPushOptions struct {
	// GitCredentialOptions selects credentials, branch, remote and timeout.
	GitCredentialOptions
	// SetUpstream sets upstream tracking; default false.
	SetUpstream bool
}

// Clone clones url into path. Depth and Timeout are validated locally.
func (g *Git) Clone(ctx context.Context, url, path string, opts *GitCloneOptions) error {
	options := GitCloneOptions{}
	if opts != nil {
		options = *opts
	}
	if options.Depth < 0 || int64(options.Depth) > math.MaxInt32 {
		return badRequest("Depth must be a positive int32, or zero to omit")
	}
	credentials, err := gitCredentials(GitCredentialOptions{
		Username: options.Username, Password: options.Password, Timeout: options.Timeout,
	})
	if err != nil {
		return err
	}
	body := wire.CloneRequest{
		Url: &url, Path: &path, Branch: gitOptional(options.Branch), CommitId: gitOptional(options.CommitID),
		Username: credentials.Username, Password: credentials.Password,
	}
	if options.Depth != 0 {
		depth := int32(options.Depth) // Checked against the wire int32 range above.
		body.Depth = &depth
	}
	if options.InsecureSkipTLS {
		body.InsecureSkipTls = &options.InsecureSkipTLS
	}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/clone", Body: body, Timeout: gitRemoteTimeout(options.Timeout),
	})
}

// Pull pulls from the named or configured remote, with a default 30-minute total deadline.
func (g *Git) Pull(ctx context.Context, path string, opts *GitCredentialOptions) error {
	options := GitCredentialOptions{}
	if opts != nil {
		options = *opts
	}
	body, err := gitCredentials(options)
	if err != nil {
		return err
	}
	body.Path = &path
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/pull", Body: body, Timeout: gitRemoteTimeout(options.Timeout),
	})
}

// Push pushes to the named or configured remote, with a default 30-minute total deadline.
func (g *Git) Push(ctx context.Context, path string, opts *GitPushOptions) error {
	options := GitPushOptions{}
	if opts != nil {
		options = *opts
	}
	body, err := gitCredentials(options.GitCredentialOptions)
	if err != nil {
		return err
	}
	body.Path = &path
	if options.SetUpstream {
		body.SetUpstream = &options.SetUpstream
	}
	return g.sandbox.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodPost, Path: "/git/push", Body: body, Timeout: gitRemoteTimeout(options.Timeout),
	})
}

func gitCredentials(options GitCredentialOptions) (wire.RepoRequest, error) {
	if options.Timeout < 0 && options.Timeout != NoTimeout {
		return wire.RepoRequest{}, badRequest("Timeout must be non-negative or NoTimeout")
	}
	return wire.RepoRequest{
		Username: gitOptional(options.Username), Password: gitOptional(options.Password),
		Branch: gitOptional(options.Branch), Remote: gitOptional(options.Remote),
	}, nil
}

func gitRemoteTimeout(timeout time.Duration) time.Duration {
	if timeout == 0 {
		return 30 * time.Minute
	}
	if boundedTimeout(timeout) == 0 {
		return NoTimeout
	}
	// Remote deadlines use the same upward millisecond rounding as TypeScript.
	return time.Duration(wireMilliseconds(timeout)) * time.Millisecond
}
