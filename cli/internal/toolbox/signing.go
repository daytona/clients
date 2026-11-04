// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	genapi "github.com/daytona/clients/cli/gen/api"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

type TransferSpec struct {
	SandboxID string
	Path      string
}

// ParseTransferSpec splits `<sandbox-id>:/path`. A single ASCII letter before
// the colon followed by an absolute path is a Windows drive path (`C:/x`,
// `D:\\y`) and is LOCAL — the scp/docker rule — on every OS, deterministically:
// a sandbox literally named with one letter can therefore never be addressed
// as `C:/path`; use its full id form instead. Sandbox ids are never one letter.
func ParseTransferSpec(value string) (*TransferSpec, bool) {
	id, filePath, ok := strings.Cut(value, ":")
	if ok && len(id) == 1 && isASCIIAlpha(id[0]) && filePath != "" && (filePath[0] == '/' || filePath[0] == '\\') {
		return nil, false
	}
	if !ok || id == "" || !strings.HasPrefix(filePath, "/") {
		return nil, false
	}
	return &TransferSpec{SandboxID: id, Path: filePath}, true
}

func isASCIIAlpha(ch byte) bool {
	return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
}

// SignedFileMaxTTL is the longest lifetime a pre-signed file URL may be
// minted with: 7 days, the same bound both SDKs enforce before any request.
const SignedFileMaxTTL = 7 * 24 * time.Hour

// signedFileNow is the clock the expiry is stamped from; a package variable
// so tests can pin it.
var signedFileNow = time.Now

// SignFileURL mints a pre-signed download (`GET`) or upload (`POST`) link for
// one sandbox path through the public API's
// `POST /sandboxes/{id}/files/sign`. The per-sandbox signing key never reaches the
// CLI: the response carries the complete download or upload URL, which is
// returned unchanged. `ttl` must lie in (0, SignedFileMaxTTL];
// the API answers 400 for an `expires_at` outside its window, 403 when the
// credential lacks `write:sandboxes` or its user left the organization, and
// 404 when the sandbox is not found or belongs to another project — each mapped by the API
// client to the CLI's usual exit codes.
func SignFileURL(ctx context.Context, apiClient *api.Client, detailClass, sandboxID, method, filePath string, ttl time.Duration) (string, error) {
	switch detailClass {
	case "linux", "macos":
	default:
		return "", cerr.Usage("signed file transfers are not available for sandbox class %q", detailClass)
	}
	method = strings.ToUpper(strings.TrimSpace(method))
	if method != http.MethodGet && method != http.MethodPost {
		return "", fmt.Errorf("unsupported signing operation %q", method)
	}
	if ttl < time.Second || ttl > SignedFileMaxTTL {
		return "", cerr.Usage("signed URL ttl must be between 1s and %s (7 days)", SignedFileMaxTTL)
	}
	body := genapi.SignSandboxFileUrlRequest{
		Method:    method,
		Path:      filePath,
		ExpiresAt: signedFileNow().Add(ttl),
	}
	var raw json.RawMessage
	if err := apiClient.Do(ctx, http.MethodPost, "/sandboxes/"+url.PathEscape(sandboxID)+"/files/sign", nil, body, &raw); err != nil {
		return "", err
	}
	return decodeSignedFileURLPayload(raw)
}

func SingleFileLocalDestination(rawPath, remotePath string) string {
	if targetDir, ok := localDirectoryIntent(rawPath); ok {
		if isWindowsVolumeRootDir(targetDir) {
			return targetDir + path.Base(remotePath)
		}
		return filepath.Join(targetDir, path.Base(remotePath))
	}
	if info, err := os.Stat(rawPath); err == nil && info.IsDir() {
		return filepath.Join(rawPath, path.Base(remotePath))
	}
	return rawPath
}

// decodeSignedFileURLPayload checks the sign response at runtime the way the
// SDKs' readSignedFileUrl does: a JSON `null`, a non-object, or a body whose
// `url` / `signature` / `expires_at` are missing, mistyped, or empty is the
// documented invalid-payload error, never a zero-value URL handed to the
// transfer. `url` must be absolute — it is used verbatim.
func decodeSignedFileURLPayload(raw json.RawMessage) (string, error) {
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(raw, &payload); err != nil || payload == nil {
		return "", invalidSignedFileURLPayloadError()
	}
	for _, field := range []string{"url", "signature", "expires_at"} {
		if _, ok := payload[field]; !ok {
			return "", invalidSignedFileURLPayloadError()
		}
	}
	var response genapi.SignedFileUrl
	if err := json.Unmarshal(raw, &response); err != nil {
		return "", invalidSignedFileURLPayloadError()
	}
	if strings.TrimSpace(response.Signature) == "" || response.ExpiresAt.IsZero() {
		return "", invalidSignedFileURLPayloadError()
	}
	signedURL := strings.TrimSpace(response.Url)
	parsed, err := url.Parse(signedURL)
	if err != nil || signedURL == "" || !parsed.IsAbs() || parsed.Host == "" {
		return "", invalidSignedFileURLPayloadError()
	}
	return signedURL, nil
}

func invalidSignedFileURLPayloadError() error {
	return cerr.New(cerr.ExitFailure, "API returned an invalid signed file URL payload")
}

// localDirectoryIntent reports whether the caller asked for a directory by
// ending the path with a separator. Only the platform's separators count:
// on POSIX a trailing backslash is part of the file name (`filepath` agrees),
// on Windows `os.PathSeparator` is `\` and `/` is accepted too.
func localDirectoryIntent(rawPath string) (string, bool) {
	trimmed := strings.TrimRight(rawPath, string(os.PathSeparator)+"/")
	if trimmed == rawPath {
		return "", false
	}
	if trimmed == "" {
		return rawPath, true
	}
	if isWindowsVolumeRootPath(trimmed) {
		return trimmed + string(rawPath[len(trimmed)]), true
	}
	return trimmed, true
}

func isWindowsVolumeRootPath(value string) bool {
	return len(value) == 2 && isASCIIAlpha(value[0]) && value[1] == ':'
}

func isWindowsVolumeRootDir(value string) bool {
	return len(value) == 3 && isWindowsVolumeRootPath(value[:2]) && (value[2] == '\\' || value[2] == '/')
}
