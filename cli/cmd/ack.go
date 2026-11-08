// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/http"
	"net/url"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// printAck prints the acknowledgement of a command that has no resource to
// print: the identifier under the field that names it plus a boolean named
// after the action, e.g. {"id": "img1", "deleted": true}. With table output
// a non-empty human message replaces the two rows (on stderr, as before).
func printAck(a *app.App, idField, id, action, human string) error {
	if human != "" && a.Format == output.FormatTable {
		a.Info("%s", human)
		return nil
	}
	return a.Printer().Object(map[string]any{idField: id, action: true}, [][2]string{{idField, id}, {action, "true"}})
}

// idOnly makes the API read a path value strictly as an id.
var idOnly = url.Values{"id_only": {"true"}}

// resolveResourceID reads the resource at base+ref, which the API accepts as
// an id or a name, and returns its id: an acknowledgement's `id` is always
// the resource's id, and the following request names exactly that resource.
func resolveResourceID[T any](ctx context.Context, client *api.Client, base, ref string, id func(T) string) (string, error) {
	var v T
	if err := client.Do(ctx, http.MethodGet, base+url.PathEscape(ref), nil, nil, &v); err != nil {
		return "", err
	}
	if resolved := id(v); resolved != "" {
		return resolved, nil
	}
	return "", cerr.New(cerr.ExitFailure, "GET %s%s: the response has no id", base, ref)
}
