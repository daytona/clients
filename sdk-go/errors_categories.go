// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// Category sentinels match errors.Is without depending on server message text.
var (
	// ErrBadRequest matches invalid input and HTTP 400.
	ErrBadRequest errorCategory = "bad request"
	// ErrAuthentication matches authentication failures (401).
	ErrAuthentication errorCategory = "authentication"
	// ErrForbidden matches permission failures (403).
	ErrForbidden errorCategory = "forbidden"
	// ErrNotFound matches missing resources (404).
	ErrNotFound errorCategory = "not found"
	// ErrTimeout matches server request timeouts and SDK client timeouts (408).
	ErrTimeout errorCategory = "timeout"
	// ErrClientTimeout matches SDK request/idle deadlines, not an HTTP 408 from the server.
	ErrClientTimeout errorCategory = "client timeout"
	// ErrConflict matches state conflicts (409).
	ErrConflict errorCategory = "conflict"
	// ErrGone matches removed resources (410).
	ErrGone errorCategory = "gone"
	// ErrUnprocessableEntity matches semantically invalid requests (422).
	ErrUnprocessableEntity errorCategory = "unprocessable entity"
	// ErrRateLimit matches throttling (429).
	ErrRateLimit errorCategory = "rate limit"
	// ErrInternalServer matches server failures, including unsupported operations (501).
	ErrInternalServer errorCategory = "internal server"
	// ErrServiceUnavailable matches temporary unavailability (503).
	ErrServiceUnavailable errorCategory = "service unavailable"
	// ErrConnection matches network failures without an HTTP response.
	ErrConnection errorCategory = "connection"
	// ErrPreconditionFailed matches revision conflicts (412): a stale If-Match
	// ETag on a conditional write, or a create-only secret write that finds the
	// secret (which also matches ErrSecretAlreadyExists). An empty, non-ASCII
	// or padded ETag is refused locally with ErrBadRequest, and the API answers
	// 400 (ErrBadRequest), not 412, for some other values it cannot parse.
	ErrPreconditionFailed errorCategory = "precondition failed"
	// ErrMethodNotAllowed matches unavailable HTTP methods (405).
	ErrMethodNotAllowed errorCategory = "method not allowed"
	// ErrPayloadTooLarge matches server or SDK body size limits (413).
	ErrPayloadTooLarge errorCategory = "payload too large"
	// ErrUnsupportedMediaType matches unsupported request media types (415).
	ErrUnsupportedMediaType errorCategory = "unsupported media type"
	// ErrPreconditionRequired matches missing conditional request headers (428).
	ErrPreconditionRequired errorCategory = "precondition required"
	// ErrNotSupported matches unimplemented features (501).
	ErrNotSupported errorCategory = "not supported"
)

const (
	// SourceAPI identifies the control plane's error catalog.
	SourceAPI = "DAYTONA_API"
	// SourceDaemon identifies the in-sandbox daemon's error catalog.
	SourceDaemon = "DAYTONA_DAEMON"
	// SourceProxy identifies the toolbox proxy's error catalog.
	SourceProxy = "DAYTONA_PROXY"
	// SourceRouter identifies the API router's error catalog.
	SourceRouter = "DAYTONA_ROUTER"
	// SourceSDK identifies locally produced failures.
	SourceSDK = "SDK"
)
