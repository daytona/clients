// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strings"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/google/uuid"
	"github.com/spf13/cobra"
)

// maxIdempotencyKeyLen is the longest idempotency key the API accepts.
const maxIdempotencyKeyLen = 256

// idempotencyKeyForName is the key a named create derives, identical to the
// SDKs' so a retry from the CLI or either SDK replays the same create:
// "name:" + name when that is printable ASCII without surrounding spaces and
// at most 256 characters, else "name:sha256:" + hex(sha256(UTF-8 name)).
func idempotencyKeyForName(name string) string {
	literal := "name:" + name
	if name == strings.TrimSpace(name) && len(literal) <= maxIdempotencyKeyLen && printableASCII(name) {
		return literal
	}
	sum := sha256.Sum256([]byte(name))
	return "name:sha256:" + hex.EncodeToString(sum[:])
}

func printableASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] < 0x20 || s[i] > 0x7e {
			return false
		}
	}
	return true
}

func checkIdempotencyKey(key, origin string) error {
	if strings.TrimSpace(key) == "" {
		return cerr.Usage("%s must not be blank; omit it to use the default key", origin)
	}
	if key != strings.TrimSpace(key) || !printableASCII(key) || len(key) > maxIdempotencyKeyLen {
		return cerr.Usage("%s must be 1 to %d printable ASCII characters without surrounding spaces", origin, maxIdempotencyKeyLen)
	}
	return nil
}

// sandboxCreateHeaders always sends an Idempotency-Key, as the SDKs and
// `reservation create` do, so a retried create never buys a second sandbox:
// --idempotency-key, else the --file body's idempotency_key, else one derived
// from the name, else a random key for this invocation.
func sandboxCreateHeaders(flags sandboxCreateFlags, cmd *cobra.Command, region string, body input.Body) (http.Header, error) {
	headers := http.Header{}
	var key string
	switch {
	case cmd.Flags().Changed("idempotency-key"):
		if err := checkIdempotencyKey(flags.idempotencyKey, "--idempotency-key"); err != nil {
			return nil, err
		}
		key = flags.idempotencyKey
	case body["idempotency_key"] != nil:
		value, ok := body["idempotency_key"].(string)
		if !ok {
			return nil, cerr.Usage("idempotency_key must be a string")
		}
		if err := checkIdempotencyKey(value, "idempotency_key"); err != nil {
			return nil, err
		}
		key = value
	default:
		if name, ok := body["name"].(string); ok && strings.TrimSpace(name) != "" {
			key = idempotencyKeyForName(name)
		} else {
			key = uuid.NewString()
		}
	}
	headers.Set("Idempotency-Key", key)
	if region != "" {
		headers.Set("x-daytona-region", region)
	}
	return headers, nil
}
