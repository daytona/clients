// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"errors"
	"net/http"
	"strings"

	"github.com/spf13/cobra"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
)

// loginOnlyFamilies are the command groups whose requests go only to
// operations that admit a user login session and no API key (user_jwt alone
// in contract/api-openapi.json). loginOnlyExceptions are the commands in
// those groups that accept an API key. TestLoginOnlyCommandsSendOnlyToLoginOnlyOperations
// fails when a command covered here sends a request an API key could make.
var (
	loginOnlyFamilies   = map[string]bool{"billing": true, "invite": true, "key": true, "member": true, "org": true, "org-key": true, "project": true, "service-account": true}
	loginOnlyExceptions = map[string]bool{"org limits": true}
)

const loginOnlyHelp = "Needs a `daytona login` session: an API key (--api-key, DAYTONA_API_KEY, or one saved with `daytona login --token`) is refused before any request."

// commandPath is the command's path below the root ("billing wallet").
func commandPath(cmd *cobra.Command) string {
	return strings.TrimPrefix(strings.TrimPrefix(cmd.CommandPath(), cmd.Root().Name()), " ")
}

// isLoginOnly reports whether cmd belongs to a login-only family and is not
// one of its exceptions. A group (a command with subcommands) only prints
// help and is never refused.
func isLoginOnly(cmd *cobra.Command) bool {
	path := commandPath(cmd)
	family, _, _ := strings.Cut(path, " ")
	return loginOnlyFamilies[family] && !loginOnlyExceptions[path] && !cmd.HasSubCommands()
}

// loginOnlyRejectedHint replaces the generic 401 hint on a login-only
// command, which sends only a login session or a JWT-shaped bearer.
const loginOnlyRejectedHint = "this command needs a `daytona login` session, and the server refused the one sent: run `daytona login` (an API key is not accepted here)"

// documentLoginOnly adds loginOnlyHelp to the help of every login-only
// command, and of every group whose commands are all login-only, and gives
// a 401 from a login-only command loginOnlyRejectedHint.
func documentLoginOnly(c *cobra.Command) bool {
	all := true
	for _, sub := range c.Commands() {
		if !sub.IsAvailableCommand() {
			continue
		}
		if !documentLoginOnly(sub) {
			all = false
		}
	}
	if c.HasSubCommands() {
		family, _, _ := strings.Cut(commandPath(c), " ")
		if !all || !loginOnlyFamilies[family] {
			return false
		}
	} else if !isLoginOnly(c) {
		return false
	} else if run := c.RunE; run != nil {
		c.RunE = func(cmd *cobra.Command, args []string) error {
			return withLoginOnlyRejectedHint(run(cmd, args))
		}
	}
	long := strings.TrimSpace(c.Long)
	if long == "" {
		long = c.Short + "."
	}
	c.Long = long + "\n\n" + loginOnlyHelp
	return true
}

// refuseStaticCredential stops a login-only command that would send a static
// credential before it selects an organization or sends a request: the
// server refuses every API key there with the same 401 as an invalid key. A
// bearer shaped like a JWT, such as a login's access token passed with
// --api-key, is sent as usual.
func refuseStaticCredential(cmd *cobra.Command, a *app.App) error {
	if !isLoginOnly(cmd) {
		return nil
	}
	var e *cerr.Error
	switch a.CredentialSource {
	case "flag":
		e = cerr.New(cerr.ExitAuth, "`daytona %s` needs a `daytona login` session and does not accept an API key (--api-key)", commandPath(cmd)).
			WithHint("drop --api-key, which replaces the saved login for this call, and sign in with `daytona login` if you have not")
	case "env":
		e = cerr.New(cerr.ExitAuth, "`daytona %s` needs a `daytona login` session and does not accept an API key (DAYTONA_API_KEY)", commandPath(cmd)).
			WithHint("unset DAYTONA_API_KEY, which replaces the saved login, and sign in with `daytona login` if you have not")
	case "profile-token":
		e = cerr.New(cerr.ExitAuth, "`daytona %s` needs a `daytona login` session and does not accept the API key saved in profile %q", commandPath(cmd), a.Profile.Name).
			WithHint("run `daytona login` to replace it with a login session, or `daytona login --profile NAME` to sign in under another profile")
	default:
		return nil
	}
	cred, err := a.Credential(cmd.Context())
	if err != nil {
		return err
	}
	if looksLikeJWT(strings.TrimSpace(cred)) {
		return nil
	}
	return e
}

// looksLikeJWT reports whether s has a JWT's shape: three non-empty
// base64url segments separated by dots. Daytona API keys contain no dot.
func looksLikeJWT(s string) bool {
	parts := strings.Split(s, ".")
	if len(parts) != 3 {
		return false
	}
	for _, part := range parts {
		if part == "" {
			return false
		}
		for _, ch := range part {
			if !(ch >= 'A' && ch <= 'Z' || ch >= 'a' && ch <= 'z' || ch >= '0' && ch <= '9' || ch == '-' || ch == '_') {
				return false
			}
		}
	}
	return true
}

// withLoginOnlyRejectedHint gives a 401 from the API loginOnlyRejectedHint.
func withLoginOnlyRejectedHint(err error) error {
	var ce *cerr.Error
	var ae *api.APIError
	if errors.As(err, &ce) && errors.As(err, &ae) && ae.Status == http.StatusUnauthorized {
		ce.Hint = loginOnlyRejectedHint
	}
	return err
}
