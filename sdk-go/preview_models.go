// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

const (
	// PreviewWarningSkipHeader skips the preview warning page for non-interactive clients.
	PreviewWarningSkipHeader = "X-Daytona-Skip-Preview-Warning"
	// PreviewWarningSkipHeaderValue is the value to send with PreviewWarningSkipHeader.
	PreviewWarningSkipHeaderValue = "true"
)

// PreviewEndpoint describes a registered sandbox port.
type PreviewEndpoint struct {
	// Port is the sandbox's TCP port.
	Port int
	// Public permits access without credentials.
	Public bool
	// URL opens the endpoint.
	URL string
	// CreatedAt is when the endpoint was registered.
	CreatedAt time.Time
}

// String returns a credential-free description.
func (PreviewEndpoint) String() string { return "daytona.PreviewEndpoint" }

// GoString returns a credential-free description.
func (p PreviewEndpoint) GoString() string { return p.String() }

// PreviewLink grants access to one preview endpoint.
type PreviewLink struct {
	// URL is the preview URL.
	URL string
	// Token is sent in X-Daytona-Preview-Token; empty means public access.
	Token string
	// ExpiresAt is zero when the token's expiry is unknown.
	// JWT expiry is advisory metadata only, not verified authorization.
	ExpiresAt time.Time
}

// String returns a credential-free description.
func (PreviewLink) String() string { return "daytona.PreviewLink" }

// GoString returns a credential-free description.
func (p PreviewLink) GoString() string { return p.String() }

// SignedPreviewLink is a self-contained URL requiring no authentication header.
type SignedPreviewLink struct {
	// URL includes the signed token in its hostname.
	URL string
	// Token is the signed preview token.
	Token string
	// ExpiresAt is the server-reported expiration.
	ExpiresAt time.Time
}

// String returns a credential-free description.
func (SignedPreviewLink) String() string { return "daytona.SignedPreviewLink" }

// GoString returns a credential-free description.
func (p SignedPreviewLink) GoString() string { return p.String() }

func parsePreviewEndpoint(raw JSONValue) (*PreviewEndpoint, error) {
	object, err := parseObject(raw, "preview endpoint")
	if err != nil {
		return nil, err
	}
	port, err := integerField(object, "port", true)
	if err != nil {
		return nil, err
	}
	isPublic, err := field[bool](object, "public", true)
	if err != nil {
		return nil, err
	}
	address, err := field[string](object, "url", true)
	if err != nil {
		return nil, err
	}
	if _, fieldErr := field[string](object, "created_at", true); fieldErr != nil {
		return nil, fieldErr
	}
	createdAt, err := timestampField(object, "created_at")
	if err != nil {
		return nil, err
	}
	return &PreviewEndpoint{Port: int(port), Public: isPublic, URL: address, CreatedAt: createdAt}, nil
}
