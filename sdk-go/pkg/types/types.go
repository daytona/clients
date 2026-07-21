// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package types

import (
	"net/http"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
)

const CodeToolboxLanguageLabel = "code-toolbox-language"

// CodeLanguage
type CodeLanguage string

const (
	CodeLanguagePython     CodeLanguage = "python"
	CodeLanguageJavaScript CodeLanguage = "javascript"
	CodeLanguageTypeScript CodeLanguage = "typescript"
)

// GpuType identifies a specific NVIDIA GPU model. Used in [Resources.GpuType]
// as an ordered preference list — the scheduler tries each in order and pins
// the sandbox/snapshot to the first that has capacity. It is an alias for the
// API client's GpuType type.
type GpuType = apiclient.GpuType

const (
	GpuTypeH100       GpuType = apiclient.GPUTYPE_H100
	GpuTypeRtxPro6000 GpuType = apiclient.GPUTYPE_RTX_PRO_6000
)

// SandboxClass determines which runners can host sandboxes created from a snapshot.
// It is an alias for the API client's SandboxClass type.
type SandboxClass = apiclient.SandboxClass

const (
	SandboxClassLinuxVM   SandboxClass = apiclient.SANDBOXCLASS_LINUX_VM
	SandboxClassContainer SandboxClass = apiclient.SANDBOXCLASS_CONTAINER
	SandboxClassAndroid   SandboxClass = apiclient.SANDBOXCLASS_ANDROID
)

// ExperimentalConfig holds experimental feature flags for the Daytona client.
type ExperimentalConfig struct {
	// Deprecated: use DaytonaConfig.OtelEnabled. Kept for backwards compatibility.
	OtelEnabled bool
}

// DaytonaConfig represents the configuration for the Daytona client.
// When a field is nil, the client will fall back to environment variables or defaults.
type DaytonaConfig struct {
	APIKey         string
	JWTToken       string
	OrganizationID string
	APIUrl         string
	Target         string
	OtelEnabled    bool // Enable OpenTelemetry tracing for SDK operations.
	// UseDeprecatedPolling observes sandbox state by legacy polling instead of
	// WebSocket event streaming. Defaults to false (event streaming). Can also be
	// enabled via the DAYTONA_USE_DEPRECATED_POLLING environment variable.
	//
	// Deprecated: polling-only mode will be removed in a future release; event
	// streaming is the default and falls back to polling automatically when
	// WebSockets are unavailable.
	UseDeprecatedPolling *bool
	// Timeout overrides the default per-request HTTP timeout (60s). A
	// non-positive value disables the client-wide timeout entirely. Executions
	// with an explicit execution timeout are not capped by this value.
	Timeout *time.Duration
	// HTTPClient supplies a custom *http.Client for API requests. It is copied
	// before use (Transport shared); Timeout, when set, overrides the copy's.
	HTTPClient   *http.Client
	Experimental *ExperimentalConfig
}

// Resources represents resource allocation for a sandbox.
type Resources struct {
	CPU     int
	GPU     int
	GpuType []GpuType
	Memory  int
	Disk    int
}

// VolumeMount represents a volume mount configuration
type VolumeMount struct {
	VolumeID  string // ID or name of the volume to mount
	MountPath string
	Subpath   *string // Optional subpath within the volume; nil = mount entire volume
}

// SandboxBaseParams contains common parameters for sandbox creation.
type SandboxBaseParams struct {
	Name                string
	User                string
	Language            CodeLanguage
	EnvVars             map[string]string
	Labels              map[string]string
	Public              bool
	AutoStopInterval    *int // nil = no auto-stop, 0 = immediate stop
	AutoPauseInterval   *int // nil = server default when AutoStopInterval is also nil (60 for non-ephemeral pause-supporting classes, with auto-stop disabled), 0 = disabled. Only supported for sandbox classes that support pausing. Not allowed for ephemeral sandboxes. At most one of AutoPauseInterval and AutoStopInterval may be non-zero.
	AutoArchiveInterval *int // nil = no auto-archive, 0 = immediate archive
	AutoDeleteInterval  *int // nil = no auto-delete, 0 = immediate delete
	TtlMinutes          *int // Wall-clock max lifetime in minutes; 0 disables TTL
	Volumes             []VolumeMount
	// Secrets maps an environment variable name to the name of an existing
	// organization secret. For each entry, the env var is injected into the
	// sandbox holding the secret's opaque placeholder, which is resolved to the
	// real value only when the sandbox connects to one of the secret's allowed
	// hosts. The referenced secrets must already exist (see [Client.Secret]).
	Secrets          map[string]string
	NetworkBlockAll  bool
	NetworkAllowList *string
	DomainAllowList  *string
	Ephemeral        bool
	// LinkedSandbox is the ID or name of an existing sandbox to link the new sandbox to.
	// The new sandbox will be scheduled on the same runner as the linked sandbox so a local
	// network can be established between them.
	// Linked sandboxes must be ephemeral (AutoDeleteInterval=0) and cannot themselves be
	// linked to another sandbox.
	LinkedSandbox string
}

// SnapshotParams represents parameters for creating a sandbox from a snapshot
type SnapshotParams struct {
	SandboxBaseParams
	Snapshot string
}

// ImageParams represents parameters for creating a sandbox from an image
type ImageParams struct {
	SandboxBaseParams
	Image     any // string or *Image
	Resources *Resources
}

// CreateSnapshotParams represents parameters for creating a snapshot
type CreateSnapshotParams struct {
	Name           string
	Image          any // string or *Image
	Resources      *Resources
	Entrypoint     []string
	SkipValidation *bool
	SandboxClass   *SandboxClass
}

// PaginatedSnapshots represents a paginated list of snapshots
type PaginatedSnapshots struct {
	Items      []*Snapshot
	Total      int
	Page       int
	TotalPages int
}

// Volume represents a Daytona volume
type Volume struct {
	ID             string    `json:"id"`
	Name           string    `json:"name"`
	OrganizationID string    `json:"organizationId"`
	State          string    `json:"state"`
	ErrorReason    *string   `json:"errorReason,omitempty"`
	CreatedAt      time.Time `json:"createdAt"`
	UpdatedAt      time.Time `json:"updatedAt"`
	LastUsedAt     time.Time `json:"lastUsedAt,omitempty"`
}

// Secret represents an organization-scoped secret.
//
// A Secret stores a write-only plaintext value that is never returned by the
// API. When referenced from a sandbox, the env var holds the opaque
// [Secret.Placeholder] token, which is resolved to the real value only for the
// secret's allowed [Secret.Hosts].
type Secret struct {
	ID          string  `json:"id"`
	Name        string  `json:"name"`
	Description *string `json:"description,omitempty"`
	// Placeholder is the opaque token injected as the env var value in sandboxes.
	Placeholder string `json:"placeholder"`
	// Hosts are the allowed hosts this secret may be sent to. Entries are exact
	// hostnames or "*." wildcards (without ports).
	Hosts     []string  `json:"hosts"`
	CreatedAt time.Time `json:"createdAt"`
	UpdatedAt time.Time `json:"updatedAt"`
}

// CreateSecretParams contains parameters for creating a secret.
type CreateSecretParams struct {
	// Name is the secret name. It must match ^[a-zA-Z_][a-zA-Z0-9_-]*$ and be
	// unique within the organization (a duplicate name returns a 409 conflict).
	Name string
	// Value is the plaintext secret value. It is write-only and never returned.
	Value string
	// Description is an optional human-readable description.
	Description *string
	// Hosts are the allowed hosts this secret may be sent to. Entries are exact
	// hostnames or "*." wildcards (without ports).
	Hosts []string
}

// UpdateSecretParams contains parameters for updating a secret. Only the
// non-nil fields are applied.
type UpdateSecretParams struct {
	// Value is the new plaintext secret value. It is write-only and never returned.
	Value *string
	// Description is an optional human-readable description.
	Description *string
	// Hosts are the allowed hosts this secret may be sent to. Entries are exact
	// hostnames or "*." wildcards (without ports).
	Hosts []string
}

// ListSecretsQuery contains query parameters for filtering, sorting, and
// paginating when listing secrets. All fields are optional.
type ListSecretsQuery struct {
	// Pagination cursor from a previous response's NextCursor
	Cursor *string
	// Number of results per page (1-200, default 100)
	Limit *int
	// Filter by partial name match
	Name *string
	// Sort by field: "name", "createdAt", or "updatedAt" (default "createdAt")
	Sort *string
	// Sort direction: "asc" or "desc" (default "desc")
	Order *string
}

// ListSecretsResponse represents a paginated list of secrets
type ListSecretsResponse struct {
	Items []*Secret
	// Total number of secrets matching the filters
	Total int
	// Cursor for the next page of results; nil when there are no further pages
	NextCursor *string
}

// Snapshot represents a Daytona snapshot
type Snapshot struct {
	ID             string     `json:"id"`
	OrganizationID string     `json:"organizationId,omitempty"`
	General        bool       `json:"general"`
	Name           string     `json:"name"`
	ImageName      string     `json:"imageName,omitempty"`
	State          string     `json:"state"`
	Size           *float64   `json:"size,omitempty"`
	Entrypoint     []string   `json:"entrypoint,omitempty"`
	CPU            int        `json:"cpu"`
	GPU            int        `json:"gpu"`
	Memory         int        `json:"mem"` // API uses "mem" not "memory"
	Disk           int        `json:"disk"`
	ErrorReason    *string    `json:"errorReason,omitempty"` // nil = success, non-nil = error reason if snapshot failed
	SkipValidation bool       `json:"skipValidation"`
	CreatedAt      time.Time  `json:"createdAt"`
	UpdatedAt      time.Time  `json:"updatedAt"`
	LastUsedAt     *time.Time `json:"lastUsedAt,omitempty"`
}

// FileInfo represents file metadata
type FileInfo struct {
	Name         string
	Path         string
	Size         int64
	Mode         string
	ModifiedTime time.Time
	IsDirectory  bool
}

// FileUpload represents a file to upload
type FileUpload struct {
	Source      any // []byte or string (path)
	Destination string
}

// FileDownloadRequest
type FileDownloadRequest struct {
	Source      string
	Destination *string // nil = download to memory (return []byte), non-nil = save to file path
}

// FileDownloadResponse represents a file download response
type FileDownloadResponse struct {
	Source string
	Result any     // []byte or string (path)
	Error  *string // nil = success, non-nil = error message
}

// GitStatus represents git repository status
type GitStatus struct {
	CurrentBranch   string
	Ahead           int
	Behind          int
	BranchPublished bool
	FileStatus      []FileStatus
	// Detached is true when HEAD is not on a branch (detached HEAD state).
	Detached bool
	// Upstream is the upstream tracking branch (e.g. "origin/main"), empty when unset.
	Upstream string
}

// FileStatus represents the status of a file in git
type FileStatus struct {
	Path   string
	Status string
}

// GitRemote describes a configured Git remote.
type GitRemote struct {
	Name string
	URL  string
}

// GitCommitResponse
type GitCommitResponse struct {
	SHA string
}

// CodeRunParams represents parameters for code execution
type CodeRunParams struct {
	Argv []string
	Env  map[string]string
}

// ExecuteResponse represents a command execution response
type ExecuteResponse struct {
	ExitCode  int
	Result    string
	Artifacts *ExecutionArtifacts // nil when no artifacts available
}

// ExecutionArtifacts represents execution output artifacts
type ExecutionArtifacts struct {
	Stdout string
	Charts []Chart
}

// ExecutionResult represents code interpreter execution result
type ExecutionResult struct {
	Stdout string
	Stderr string
	Charts []Chart         // Optional charts from matplotlib
	Error  *ExecutionError // nil = success, non-nil = execution failed
}

// ExecutionError represents a code execution error
type ExecutionError struct {
	Name      string
	Value     string
	Traceback *string // Optional stack trace; nil when not available
}

// OutputMessage represents an output message
type OutputMessage struct {
	Type      string `json:"type"`
	Text      string `json:"text"`
	Name      string `json:"name"`
	Value     string `json:"value"`
	Traceback string `json:"traceback"`
}

// PtySize represents terminal dimensions
type PtySize struct {
	Rows int
	Cols int
}

// PtyResult represents PTY session exit information
type PtyResult struct {
	ExitCode *int    // nil = process still running, non-nil = exit code
	Error    *string // nil = success, non-nil = error message
}

// PtySessionInfo represents PTY session information
type PtySessionInfo struct {
	ID        string
	Active    bool
	CWD       string // Current working directory; may be empty unavailable
	Cols      int
	Rows      int
	ProcessID *int // Process ID; may be nil if unavailable
	CreatedAt time.Time
}

// ScreenshotRegion represents a screenshot region
type ScreenshotRegion struct {
	X      int
	Y      int
	Width  int
	Height int
}

type ScreenshotOptions struct {
	ShowCursor *bool    // nil = default, true = show, false = hide
	Format     *string  // nil = default format (PNG), or "jpeg", "webp", etc.
	Quality    *int     // nil = default quality, 0-100 for JPEG/WebP
	Scale      *float64 // nil = 1.0, scaling factor for the screenshot
}

type ScreenshotResponse struct {
	Image     string // base64-encoded image data
	Width     int
	Height    int
	SizeBytes *int // Size in bytes
}

type LspLanguageID string

const (
	LspLanguagePython     LspLanguageID = "python"
	LspLanguageJavaScript LspLanguageID = "javascript"
	LspLanguageTypeScript LspLanguageID = "typescript"
)

// Position represents a position in a document
type Position struct {
	Line      int // zero-based
	Character int // zero-based
}

type Chart = toolbox.Chart

// PreviewLink contains the URL and authentication token for a sandbox preview.
type PreviewLink struct {
	URL   string
	Token string
}

// SignedPreviewLink contains the signed URL, authentication token, port, and sandbox ID for a sandbox preview.
type SignedPreviewLink struct {
	SandboxID string
	Port      int
	Token     string
	URL       string
}
