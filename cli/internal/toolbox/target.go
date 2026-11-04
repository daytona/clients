// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"net/http"
	"strings"

	genapi "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

type Target struct {
	Detail         genapi.Sandbox
	ToolboxBaseURL string
	SSHHost        string
	SSHPort        int
}

var sandboxIDPlaceholders = []string{"{sandboxId}", "{sandbox_id}"}

func TargetFromDetail(detail genapi.Sandbox) (*Target, error) {
	toolboxProxyURL := ""
	if detail.ToolboxProxyUrl != nil {
		toolboxProxyURL = strings.TrimRight(strings.TrimSpace(*detail.ToolboxProxyUrl), "/")
	}
	if toolboxProxyURL == "" {
		return nil, cerr.New(cerr.ExitConflict, "sandbox %s has no toolbox proxy URL", detail.Id)
	}
	toolboxBaseURL, err := scopedToolboxURL(toolboxProxyURL, detail.Id)
	if err != nil {
		return nil, err
	}
	target := &Target{
		Detail:         detail,
		ToolboxBaseURL: toolboxBaseURL,
	}
	target.SSHHost, target.SSHPort = resolveSSHGateway(detail)
	return target, nil
}

func scopedToolboxURL(baseURL, sandboxID string) (string, error) {
	trimmed := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if hasSandboxIDPlaceholder(trimmed) {
		return expandSandboxIDPlaceholder(trimmed, sandboxID)
	}
	return trimmed + "/" + strictPathSegment(sandboxID), nil
}

func hasSandboxIDPlaceholder(rawURL string) bool {
	for _, placeholder := range sandboxIDPlaceholders {
		if strings.Contains(rawURL, placeholder) {
			return true
		}
	}
	return false
}

func expandSandboxIDPlaceholder(rawURL, sandboxID string) (string, error) {
	if err := assertPlaceholderInPath(rawURL); err != nil {
		return "", err
	}
	encoded := strictPathSegment(sandboxID)
	expanded := rawURL
	for _, placeholder := range sandboxIDPlaceholders {
		expanded = strings.ReplaceAll(expanded, placeholder, encoded)
	}
	return expanded, nil
}

func assertPlaceholderInPath(rawURL string) error {
	schemeEnd := strings.Index(rawURL, "://")
	from := 0
	if schemeEnd >= 0 {
		from = schemeEnd + 3
	}
	pathStart := len(rawURL)
	for _, delimiter := range []string{"/", "?", "#"} {
		if index := strings.Index(rawURL[from:], delimiter); index >= 0 && from+index < pathStart {
			pathStart = from + index
		}
	}
	authority := rawURL[:pathStart]
	rest := rawURL[pathStart:]
	pathEnd := 0
	if strings.HasPrefix(rest, "/") {
		pathEnd = len(rest)
		for _, delimiter := range []string{"?", "#"} {
			if index := strings.Index(rest, delimiter); index >= 0 && index < pathEnd {
				pathEnd = index
			}
		}
	}
	for _, part := range []string{authority, rest[pathEnd:]} {
		if hasSandboxIDPlaceholder(part) {
			return cerr.New(cerr.ExitFailure, "the {sandboxId} placeholder in a toolbox URL is only supported in the path (e.g. https://proxy.example/test-tools/{sandboxId}), not in the scheme, host, query or fragment")
		}
	}
	return nil
}

// ResolveSSHGateway fills the SSH gateway from GET /config when the sandbox
// response carried no complete gateway address. A failing /config is an error
// the caller reports — not a silent "no gateway"
// that later surfaces as a misleading ssh connection failure.
func ResolveSSHGateway(ctx context.Context, apiClient *api.Client, target *Target) error {
	if target.SSHHost != "" && target.SSHPort > 0 {
		return nil
	}
	config, err := fetchRuntimeConfig(ctx, apiClient)
	if err != nil {
		return err
	}
	if config.SshGateway == nil || strings.TrimSpace(config.SshGateway.Host) == "" || config.SshGateway.Port <= 0 {
		return cerr.New(cerr.ExitConflict, "this deployment publishes no SSH gateway for sandbox %s", target.Detail.Id).
			WithHint("neither the sandbox nor GET /config publishes a complete SSH gateway address; use `sandbox exec` instead")
	}
	target.SSHHost = strings.TrimSpace(config.SshGateway.Host)
	target.SSHPort = int(config.SshGateway.Port)
	return nil
}

func resolveSSHGateway(detail genapi.Sandbox) (string, int) {
	if detail.SshGatewayHost == nil || strings.TrimSpace(*detail.SshGatewayHost) == "" || detail.SshGatewayPort == nil || *detail.SshGatewayPort <= 0 {
		return "", 0
	}
	return strings.TrimSpace(*detail.SshGatewayHost), int(*detail.SshGatewayPort)
}

// fetchRuntimeConfig reads GET /config through the shared transport (error
// mapping, timeouts, debug logging, headers) — unauthenticated, so the
// credential is dropped.
func fetchRuntimeConfig(ctx context.Context, apiClient *api.Client) (*genapi.Config, error) {
	var cfg genapi.Config
	if err := apiClient.WithCredential("").Do(ctx, http.MethodGet, "/config", nil, nil, &cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}

// strictPathSegment is the SDKs' path-segment encoder (TS `strictUriEncode`,
// Python `quote(id, safe="")`): RFC 3986 unreserved characters (ALPHA DIGIT
// - . _ ~) are kept, everything else — including the sub-delims
// `url.PathEscape` leaves alone (`!'()*` `:@&=+$,;`) — is percent-encoded,
// keeping encoded sandbox paths byte-identical across all three clients.
func strictPathSegment(s string) string {
	const hex = "0123456789ABCDEF"
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case 'A' <= c && c <= 'Z', 'a' <= c && c <= 'z', '0' <= c && c <= '9', c == '-', c == '.', c == '_', c == '~':
			b.WriteByte(c)
		default:
			b.WriteByte('%')
			b.WriteByte(hex[c>>4])
			b.WriteByte(hex[c&15])
		}
	}
	return b.String()
}
