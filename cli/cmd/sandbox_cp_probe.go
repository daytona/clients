// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"net/http"
	"net/url"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

// remoteIsDir reports whether path is an existing directory in the sandbox,
// so a file copied onto it lands inside it, as with POSIX cp. Only a missing
// file leaves the destination as given; other lookup failures stop the copy.
func remoteIsDir(ctx context.Context, toolClient *toolboxclient.Client, remotePath string) (bool, error) {
	var info tbgen.FileInfo
	if err := toolClient.GetJSON(ctx, "/files/info", url.Values{"path": {remotePath}}, &info); err != nil {
		var daemonErr *toolboxclient.DaemonError
		if errors.As(err, &daemonErr) && daemonErr.Status == http.StatusNotFound && daemonErr.Code == "FILE_NOT_FOUND" {
			return false, nil
		}
		return false, err
	}
	return info.IsDir, nil
}
