// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/daytona/clients/sdk-go/pkg/errors"
)

const (
	fileURLSignatureV1Prefix = "v1_"
	defaultFileURLTTLSeconds = 3600
	signingKeyCacheTTL       = 15 * time.Second
)

func computeFileUrlSignature(signingKey, method, path string, expires int64) string {
	canonical := fmt.Sprintf("v1:files:%s:%s:%d", method, path, expires)
	mac := hmac.New(sha256.New, []byte(signingKey))
	_, _ = mac.Write([]byte(canonical))

	return fileURLSignatureV1Prefix + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func resolveExpires(ttlSeconds *int) int64 {
	if ttlSeconds == nil {
		return time.Now().Unix() + defaultFileURLTTLSeconds
	}

	if *ttlSeconds <= 0 {
		return 0
	}

	return time.Now().Unix() + int64(*ttlSeconds)
}

func buildSignedFileUrl(toolboxProxyUrl, sandboxId, operationPath, method, filePath, signingKey string, ttlSeconds *int) (string, error) {
	if signingKey == "" {
		return "", errors.NewDaytonaError("Sandbox signing key is not available. Call RefreshData or fetch the sandbox by ID to load it.", 0, nil)
	}
	if toolboxProxyUrl == "" {
		return "", errors.NewDaytonaError("Sandbox toolbox proxy URL is not available. Call RefreshData or fetch the sandbox by ID to load it.", 0, nil)
	}

	expires := resolveExpires(ttlSeconds)

	signature := computeFileUrlSignature(signingKey, method, filePath, expires)
	query := url.Values{}
	query.Set("path", filePath)
	query.Set("expires", fmt.Sprintf("%d", expires))
	query.Set("signature", signature)

	return fmt.Sprintf("%s/%s%s?%s", strings.TrimRight(toolboxProxyUrl, "/"), sandboxId, operationPath, query.Encode()), nil
}
