// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"errors"
	"net/url"
)

// signedURLParseError retains the parse reason, never the signed URL that
// net/http includes in its outer error. Transport errors use redactURLError.
func signedURLParseError(err error) error {
	var urlErr *url.Error
	if errors.As(err, &urlErr) {
		return urlErr.Err
	}
	return err
}
