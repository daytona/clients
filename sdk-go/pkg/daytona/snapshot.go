// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bufio"
	"context"
	stderrors "errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	apiclient "github.com/daytona/clients/api-client-go"
	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/types"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/trace"
)

// snapshotIDRegex mirrors the server's uuid.validate() check
// (RFC 4122 v1-5 + the nil UUID) so client and server always agree on what
// is ID-shaped. It is intentionally stricter than google/uuid's Validate,
// which accepts dashless, braced, and URN forms and all versions — using
// that looser check here would turn the name-fallback path into 400s at the
// server, because DELETE/activate accept UUIDs only.
var snapshotIDRegex = regexp.MustCompile(`^(?i:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|00000000-0000-0000-0000-000000000000)$`)

// isSnapshotID reports whether s is a canonical RFC 4122 v1-5 UUID or the
// nil UUID (00000000-...). Snapshot IDs are UUIDs, but snapshot NAMES may
// also be UUID-shaped strings, so callers still have to disambiguate.
func isSnapshotID(s string) bool {
	return snapshotIDRegex.MatchString(s)
}

// SnapshotService provides snapshot (image template) management operations.
//
// SnapshotService enables creating, managing, and deleting snapshots that serve as
// templates for sandboxes. Snapshots can be built from Docker images or custom
// [DockerImage] definitions with build contexts. Access through [Client.Snapshot].
//
// Example:
//
//	// Create a snapshot from an existing image
//	snapshot, logChan, err := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
//	    Name:  "my-python-env",
//	    Image: "python:3.11-slim",
//	})
//	if err != nil {
//	    return err
//	}
//
//	// Stream build logs
//	for log := range logChan {
//	    fmt.Println(log)
//	}
//
//	// Create a snapshot from a custom Image definition
//	image := daytona.Base("python:3.11-slim").
//	    PipInstall([]string{"numpy", "pandas"}).
//	    Workdir("/app")
//	snapshot, logChan, err := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
//	    Name:  "custom-python-env",
//	    Image: image,
//	})
type SnapshotService struct {
	client *Client
	otel   *otelState
}

// NewSnapshotService creates a new SnapshotService.
//
// This is typically called internally by the SDK when creating a [Client].
// Users should access SnapshotService through [Client.Snapshot] rather than
// creating it directly.
func NewSnapshotService(client *Client) *SnapshotService {
	return &SnapshotService{
		client: client,
		otel:   client.Otel,
	}
}

// List returns snapshots with optional pagination.
//
// Parameters:
//   - page: Page number (1-indexed), nil for first page
//   - limit: Maximum snapshots per page, nil for default
//
// Example:
//
//	pageNumber, pageSize := 2, 10
//	page, err := client.Snapshot.List(ctx, &pageNumber, &pageSize)
//	if err != nil {
//	    return err
//	}
//
//	fmt.Printf("Page %d of %d (%d snapshots total)\n", page.Page, page.TotalPages, page.Total)
//	for _, snapshot := range page.Items {
//	    fmt.Printf("%s (%s)\n", snapshot.Name, snapshot.ImageName)
//	}
//
// Returns [types.PaginatedSnapshots] containing the snapshots and pagination info.
func (s *SnapshotService) List(ctx context.Context, page *int, limit *int) (*types.PaginatedSnapshots, error) {
	return withInstrumentation(ctx, s.otel, "Snapshot", "List", func(ctx context.Context) (*types.PaginatedSnapshots, error) {
		req := s.client.apiClient.SnapshotsAPI.GetAllSnapshots(s.client.getAuthContext(ctx))

		if page != nil {
			req = req.Page(float32(*page))
		}
		if limit != nil {
			req = req.Limit(float32(*limit))
		}

		result, httpResp, err := req.Execute()
		if err != nil {
			return nil, s.client.handleAPIError(err, httpResp)
		}

		// Map API response to SDK types
		snapshots := make([]*types.Snapshot, len(result.Items))
		for i, item := range result.Items {
			snapshots[i] = mapSnapshotFromAPI(&item)
		}

		return &types.PaginatedSnapshots{
			Items:      snapshots,
			Total:      int(result.GetTotal()),
			Page:       int(result.GetPage()),
			TotalPages: int(result.GetTotalPages()),
		}, nil
	})
}

// Get retrieves a snapshot by name or ID.
//
// Parameters:
//   - nameOrID: The snapshot name or unique ID
//
// Example:
//
//	snapshot, err := client.Snapshot.Get(ctx, "my-python-env")
//	if err != nil {
//	    return err
//	}
//	fmt.Printf("Snapshot %s: %s\n", snapshot.Name, snapshot.State)
//
// Returns the [types.Snapshot] or an error if not found.
func (s *SnapshotService) Get(ctx context.Context, nameOrID string) (*types.Snapshot, error) {
	return withInstrumentation(ctx, s.otel, "Snapshot", "Get", func(ctx context.Context) (*types.Snapshot, error) {
		result, httpResp, err := s.client.apiClient.SnapshotsAPI.GetSnapshot(
			s.client.getAuthContext(ctx),
			nameOrID,
		).Execute()

		if err != nil {
			return nil, s.client.handleAPIError(err, httpResp)
		}

		return mapSnapshotFromAPI(result), nil
	})
}

// Create builds a new snapshot from an image and streams build logs.
//
// The image parameter can be either a Docker image reference string (e.g., "python:3.11")
// or an [DockerImage] builder object for custom Dockerfile definitions.
//
// Parameters:
//   - params: Snapshot creation parameters including name, image, resources, and entrypoint
//
// Example:
//
//	// Create from Docker Hub image
//	snapshot, logChan, err := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
//	    Name:  "my-env",
//	    Image: "python:3.11-slim",
//	})
//	if err != nil {
//	    return err
//	}
//
//	// Stream build logs
//	for log := range logChan {
//	    fmt.Println(log)
//	}
//
//	// Create with custom image and resources
//	image := daytona.Base("python:3.11").PipInstall([]string{"numpy"})
//	snapshot, logChan, err := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
//	    Name:  "custom-env",
//	    Image: image,
//	    Resources: &types.Resources{CPU: 2, Memory: 4096},
//	})
//
// Returns the created [types.Snapshot], a channel for streaming build logs, or an error.
// The log channel is closed when the build completes or fails.
func (s *SnapshotService) Create(ctx context.Context, params *types.CreateSnapshotParams) (*types.Snapshot, <-chan string, error) {
	if s.otel == nil {
		return s.doCreate(ctx, params)
	}
	ctx, span := s.otel.tracer.Start(ctx, "Snapshot.Create",
		trace.WithAttributes(
			attribute.String("component", "Snapshot"),
			attribute.String("method", "Create"),
		),
	)
	start := time.Now()
	result, logChan, err := s.doCreate(ctx, params)
	duration := float64(time.Since(start).Milliseconds())
	status := "success"
	if err != nil {
		status = "error"
		span.RecordError(err)
	}
	span.End()
	metricName := "snapshot_create_duration"
	if h, hErr := s.otel.getHistogram(metricName); hErr == nil {
		h.Record(ctx, duration,
			metric.WithAttributes(
				attribute.String("component", "Snapshot"),
				attribute.String("method", "Create"),
				attribute.String("status", status),
			),
		)
	}
	return result, logChan, err
}

func (s *SnapshotService) doCreate(ctx context.Context, params *types.CreateSnapshotParams) (*types.Snapshot, <-chan string, error) {
	// Build create snapshot request
	req := s.client.apiClient.SnapshotsAPI.CreateSnapshot(s.client.getAuthContext(ctx))

	createReq := apiclient.CreateSnapshot{
		Name: params.Name,
	}

	// Handle image
	if imageStr, ok := params.Image.(string); ok {
		// When image is a string, use ImageName field (not BuildInfo)
		createReq.ImageName = &imageStr
	} else if img, ok := params.Image.(*DockerImage); ok {
		// Process Image builder
		contextHashes, err := s.processImageContext(ctx, img)
		if err != nil {
			return nil, nil, err
		}

		dockerfileContent := img.Dockerfile()
		createReq.BuildInfo = &apiclient.CreateBuildInfo{
			DockerfileContent: dockerfileContent,
			ContextHashes:     contextHashes,
		}
	}

	// Handle resources
	if params.Resources != nil {
		if params.Resources.CPU > 0 {
			cpu := int32(params.Resources.CPU)
			createReq.Cpu = &cpu
		}
		if params.Resources.GPU > 0 {
			gpu := int32(params.Resources.GPU)
			createReq.Gpu = &gpu
		}
		if len(params.Resources.GpuType) > 0 {
			createReq.GpuType = params.Resources.GpuType
		}
		if params.Resources.Memory > 0 {
			memory := int32(params.Resources.Memory)
			createReq.Memory = &memory
		}
		if params.Resources.Disk > 0 {
			disk := int32(params.Resources.Disk)
			createReq.Disk = &disk
		}
	}

	// Handle entrypoint
	if len(params.Entrypoint) > 0 {
		createReq.Entrypoint = params.Entrypoint
	}

	if params.SandboxClass != nil {
		createReq.SandboxClass = params.SandboxClass
	}

	result, httpResp, err := req.CreateSnapshot(createReq).Execute()
	if err != nil {
		return nil, nil, s.client.handleAPIError(err, httpResp)
	}

	// Create a buffered channel for log streaming
	logChan := make(chan string, 100)

	// Start log streaming in a background goroutine
	go func() {
		defer close(logChan)
		if err := s.streamSnapshotBuildLogs(ctx, result, logChan); err != nil {
			// Send error as final log message
			select {
			case logChan <- fmt.Sprintf("Error: %v", err):
			case <-ctx.Done():
			}
		}
	}()

	return mapSnapshotFromAPI(result), logChan, nil
}

// processImageContext processes image contexts and uploads them to object storage
func (s *SnapshotService) processImageContext(ctx context.Context, image *DockerImage) ([]string, error) {
	contexts := image.Contexts()
	if len(contexts) == 0 {
		return []string{}, nil
	}

	// Get push access credentials
	creds, err := s.client.getPushAccessCredentials(ctx)
	if err != nil {
		return nil, err
	}

	// Create object storage client
	objStorage := NewObjectStorage(objectStorageConfig{
		EndpointURL:     creds.StorageURL,
		AccessKeyID:     creds.AccessKey,
		SecretAccessKey: creds.Secret,
		SessionToken:    &creds.SessionToken,
		BucketName:      creds.Bucket,
		Region:          creds.Region,
	})

	// Upload each context
	contextHashes := make([]string, 0, len(contexts))
	for _, context := range contexts {
		hash, err := objStorage.Upload(ctx, context.SourcePath, creds.OrganizationID, context.ArchivePath)
		if err != nil {
			return nil, err
		}
		contextHashes = append(contextHashes, hash)
	}

	return contextHashes, nil
}

// Delete permanently removes a snapshot.
//
// Sandboxes created from this snapshot will continue to work, but no new sandboxes
// can be created from it after deletion.
//
// Parameters:
//   - snapshot: The snapshot to delete
//
// Example:
//
//	err := client.Snapshot.Delete(ctx, snapshot)
//	if err != nil {
//	    return err
//	}
//
// Returns an error if deletion fails.
func (s *SnapshotService) Delete(ctx context.Context, snapshot *types.Snapshot) error {
	return withInstrumentationVoid(ctx, s.otel, "Snapshot", "Delete", func(ctx context.Context) error {
		httpResp, err := s.client.apiClient.SnapshotsAPI.RemoveSnapshot(
			s.client.getAuthContext(ctx),
			snapshot.ID,
		).Execute()

		if err != nil {
			return s.client.handleAPIError(err, httpResp)
		}

		return nil
	})
}

// resolveSnapshotIDForIDOnlyOp turns a name-or-ID reference into the concrete
// snapshot ID that ID-only endpoints (DELETE, activate) can accept.
//
// The algorithm matches the TypeScript and Python SDKs: if the reference is
// UUID-shaped, try the ID-only op optimistically first (idOp) — the server
// accepts UUIDs there and we save a round trip. If that returns 404, the
// reference may be a UUID-formatted NAME (snapshot names may be UUID-shaped),
// so fall through to GET /snapshots/{nameOrID} which the server resolves by
// ID or name, and return the resolved ID. Non-UUID references skip the
// optimistic call and go straight to GET.
//
// idOp is called at most once per invocation; on a non-404 error it is
// returned to the caller unchanged. The bool return indicates whether idOp
// was invoked and fully handled the operation (i.e. the caller is done).
func (s *SnapshotService) resolveSnapshotIDForIDOnlyOp(
	ctx context.Context,
	nameOrID string,
	idOp func(ctx context.Context, id string) (*http.Response, error),
) (id string, done bool, err error) {
	if isSnapshotID(nameOrID) {
		httpResp, opErr := idOp(ctx, nameOrID)
		if opErr == nil {
			return nameOrID, true, nil
		}
		convertedErr := s.client.handleAPIError(opErr, httpResp)
		if !stderrors.Is(convertedErr, sdkerrors.ErrNotFound) {
			return "", true, convertedErr
		}
		// 404 → nameOrID may be a UUID-formatted NAME; fall through to resolve.
	}

	result, httpResp, getErr := s.client.apiClient.SnapshotsAPI.GetSnapshot(
		s.client.getAuthContext(ctx),
		nameOrID,
	).Execute()
	if getErr != nil {
		return "", true, s.client.handleAPIError(getErr, httpResp)
	}
	return result.GetId(), false, nil
}

// DeleteByNameOrID permanently removes a snapshot referenced by name or ID.
//
// The reference may be either the snapshot's ID (a UUID) or its name. Because
// snapshot names may themselves be UUID-shaped, the SDK cannot decide upfront
// which one was intended; it uses an optimistic-then-fallback strategy that
// mirrors the TypeScript and Python SDKs.
//
// Call-count behavior:
//   - Plain UUID that IS the ID: 1 request (DELETE only).
//   - Non-UUID name: 2 requests (GET then DELETE).
//   - UUID-shaped string that turns out to be a NAME: 3 requests
//     (DELETE returns 404 → GET resolves the name → DELETE by resolved ID).
//
// Sandboxes created from this snapshot will continue to work, but no new
// sandboxes can be created from it after deletion.
//
// Parameters:
//   - nameOrID: The snapshot's ID or name
//
// Example:
//
//	err := client.Snapshot.DeleteByNameOrID(ctx, "my-python-env")
//	if err != nil {
//	    return err
//	}
//
// Returns an error if the snapshot cannot be found or deletion fails.
func (s *SnapshotService) DeleteByNameOrID(ctx context.Context, nameOrID string) error {
	return withInstrumentationVoid(ctx, s.otel, "Snapshot", "DeleteByNameOrID", func(ctx context.Context) error {
		removeOp := func(ctx context.Context, id string) (*http.Response, error) {
			return s.client.apiClient.SnapshotsAPI.RemoveSnapshot(
				s.client.getAuthContext(ctx),
				id,
			).Execute()
		}

		resolvedID, done, err := s.resolveSnapshotIDForIDOnlyOp(ctx, nameOrID, removeOp)
		if err != nil {
			return err
		}
		if done {
			return nil
		}

		httpResp, err := removeOp(ctx, resolvedID)
		if err != nil {
			return s.client.handleAPIError(err, httpResp)
		}
		return nil
	})
}

// Activate transitions a snapshot into the ACTIVE state so new sandboxes can
// be created from it.
//
// The reference may be either the snapshot's ID (a UUID) or its name. Because
// snapshot names may themselves be UUID-shaped, the SDK cannot decide upfront
// which one was intended; it uses an optimistic-then-fallback strategy that
// mirrors the TypeScript and Python SDKs.
//
// Call-count behavior:
//   - Plain UUID that IS the ID: 1 request (POST /snapshots/{id}/activate only).
//   - Non-UUID name: 2 requests (GET then POST).
//   - UUID-shaped string that turns out to be a NAME: 3 requests
//     (POST returns 404 → GET resolves the name → POST by resolved ID).
//
// Parameters:
//   - nameOrID: The snapshot's ID or name
//
// Example:
//
//	snapshot, err := client.Snapshot.Activate(ctx, "my-python-env")
//	if err != nil {
//	    return err
//	}
//	fmt.Printf("Snapshot %s: %s\n", snapshot.Name, snapshot.State)
//
// Returns the activated [types.Snapshot] or an error if the snapshot cannot
// be found or activation fails.
func (s *SnapshotService) Activate(ctx context.Context, nameOrID string) (*types.Snapshot, error) {
	return withInstrumentation(ctx, s.otel, "Snapshot", "Activate", func(ctx context.Context) (*types.Snapshot, error) {
		var activatedDto *apiclient.SnapshotDto

		activateOp := func(ctx context.Context, id string) (*http.Response, error) {
			result, httpResp, err := s.client.apiClient.SnapshotsAPI.ActivateSnapshot(
				s.client.getAuthContext(ctx),
				id,
			).Execute()
			if err == nil {
				activatedDto = result
			}
			return httpResp, err
		}

		resolvedID, done, err := s.resolveSnapshotIDForIDOnlyOp(ctx, nameOrID, activateOp)
		if err != nil {
			return nil, err
		}
		if done {
			return mapSnapshotFromAPI(activatedDto), nil
		}

		httpResp, err := activateOp(ctx, resolvedID)
		if err != nil {
			return nil, s.client.handleAPIError(err, httpResp)
		}
		return mapSnapshotFromAPI(activatedDto), nil
	})
}

// streamSnapshotBuildLogs streams build logs for a snapshot until it reaches a terminal state
func (s *SnapshotService) streamSnapshotBuildLogs(ctx context.Context, snapshot *apiclient.SnapshotDto, logChan chan<- string) error {
	terminalStates := map[apiclient.SnapshotState]bool{
		apiclient.SNAPSHOTSTATE_ACTIVE:       true,
		apiclient.SNAPSHOTSTATE_ERROR:        true,
		apiclient.SNAPSHOTSTATE_BUILD_FAILED: true,
	}

	// Send initial log message
	select {
	case logChan <- fmt.Sprintf("Creating snapshot %s (%s)", snapshot.GetName(), snapshot.GetState()):
	case <-ctx.Done():
		return ctx.Err()
	}

	// If already in terminal state, try to get any available build logs and return
	if terminalStates[snapshot.GetState()] {
		// Attempt to stream any logs that might be available
		err := s.streamLogsHTTP(ctx, snapshot.GetId(), logChan)
		if err != nil {
			return err
		}

		// Send final message
		if snapshot.GetState() == apiclient.SNAPSHOTSTATE_ACTIVE {
			select {
			case logChan <- fmt.Sprintf("Created snapshot %s (%s)", snapshot.GetName(), snapshot.GetState()):
			case <-ctx.Done():
			}
		}
		return nil
	}

	// Start log streaming in a goroutine if not in terminal state
	var wg sync.WaitGroup
	var streamErr error
	streamCtx, cancelStream := context.WithCancel(ctx)
	defer cancelStream()

	if !terminalStates[snapshot.GetState()] && snapshot.GetState() != apiclient.SNAPSHOTSTATE_PENDING {
		wg.Go(func() {
			streamErr = s.streamLogsHTTP(streamCtx, snapshot.GetId(), logChan)
		})
	}

	// Poll snapshot state until terminal state is reached
	previousState := snapshot.GetState()
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	for !terminalStates[snapshot.GetState()] {
		select {
		case <-ctx.Done():
			cancelStream()
			wg.Wait()
			return ctx.Err()
		case <-ticker.C:
			// Get updated snapshot state
			updatedSnapshot, httpResp, err := s.client.apiClient.SnapshotsAPI.GetSnapshot(
				s.client.getAuthContext(ctx),
				snapshot.GetId(),
			).Execute()
			if err != nil {
				cancelStream()
				wg.Wait()
				return s.client.handleAPIError(err, httpResp)
			}
			snapshot = updatedSnapshot

			// If state changed and we need to start log streaming
			if previousState != snapshot.GetState() {
				select {
				case logChan <- fmt.Sprintf("Creating snapshot %s (%s)", snapshot.GetName(), snapshot.GetState()):
				case <-ctx.Done():
					cancelStream()
					wg.Wait()
					return ctx.Err()
				}

				// Start log streaming if not already running and not in terminal state
				if previousState == apiclient.SNAPSHOTSTATE_PENDING &&
					!terminalStates[snapshot.GetState()] &&
					snapshot.GetState() != apiclient.SNAPSHOTSTATE_PENDING {
					wg.Go(func() {
						streamErr = s.streamLogsHTTP(streamCtx, snapshot.GetId(), logChan)
					})
				}
				previousState = snapshot.GetState()
			}
		}
	}

	// Cancel streaming and wait for goroutine to finish
	cancelStream()
	wg.Wait()

	// Send final log message
	if snapshot.GetState() == apiclient.SNAPSHOTSTATE_ACTIVE {
		select {
		case logChan <- fmt.Sprintf("Created snapshot %s (%s)", snapshot.GetName(), snapshot.GetState()):
		case <-ctx.Done():
			return ctx.Err()
		}
	}

	// Check for streaming errors (ignore context cancellation as it's expected when stopping the stream)
	if streamErr != nil && !stderrors.Is(streamErr, context.Canceled) {
		return streamErr
	}

	// Check for build errors
	if snapshot.GetState() == apiclient.SNAPSHOTSTATE_ERROR || snapshot.GetState() == apiclient.SNAPSHOTSTATE_BUILD_FAILED {
		errorReason := "Unknown error"
		if reason, ok := snapshot.GetErrorReasonOk(); ok && reason != nil {
			errorReason = *reason
		}
		return fmt.Errorf("failed to create snapshot %s, reason: %s", snapshot.GetName(), errorReason)
	}

	return nil
}

// streamLogsHTTP streams logs from the snapshot build logs endpoint to a channel
func (s *SnapshotService) streamLogsHTTP(ctx context.Context, snapshotID string, logChan chan<- string) error {
	// Build the URL for the build logs endpoint
	cfg := s.client.apiClient.GetConfig()
	baseURL := cfg.Servers[0].URL
	url := fmt.Sprintf("%s/snapshots/%s/build-logs?follow=true", baseURL, snapshotID)

	// Create HTTP request
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	// Add authentication and headers
	token := s.client.apiKey
	if token == "" {
		token = s.client.jwtToken
	}
	httpReq.Header.Set("Authorization", "Bearer "+token)

	// Add default headers from API client config
	for key, value := range cfg.DefaultHeader {
		httpReq.Header.Set(key, value)
	}

	// Add organization header if using JWT
	if s.client.jwtToken != "" && s.client.organizationID != "" {
		httpReq.Header.Set("X-Daytona-Organization-ID", s.client.organizationID)
	}

	// Execute the HTTP request
	resp, err := cfg.HTTPClient.Do(httpReq)
	if err != nil {
		return fmt.Errorf("failed to execute request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("unexpected status code: %d", resp.StatusCode)
	}

	// Stream the response line by line
	reader := bufio.NewReader(resp.Body)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
			line, err := reader.ReadString('\n')
			if err != nil {
				if err == io.EOF {
					// End of stream
					if line != "" {
						select {
						case logChan <- strings.TrimRight(line, "\n\r"):
						case <-ctx.Done():
							return ctx.Err()
						}
					}
					return nil
				}
				return fmt.Errorf("failed to read log line: %w", err)
			}

			// Send log line to channel (remove trailing newline)
			if line != "" {
				select {
				case logChan <- strings.TrimRight(line, "\n\r"):
				case <-ctx.Done():
					return ctx.Err()
				}
			}
		}
	}
}

// mapSnapshotFromAPI converts API snapshot to SDK snapshot type
func mapSnapshotFromAPI(apiSnapshot *apiclient.SnapshotDto) *types.Snapshot {
	snapshot := &types.Snapshot{
		ID:         apiSnapshot.GetId(),
		General:    apiSnapshot.GetGeneral(),
		Name:       apiSnapshot.GetName(),
		State:      string(apiSnapshot.GetState()),
		Entrypoint: apiSnapshot.GetEntrypoint(),
		CPU:        int(apiSnapshot.GetCpu()),
		GPU:        int(apiSnapshot.GetGpu()),
		Memory:     int(apiSnapshot.GetMem()),
		Disk:       int(apiSnapshot.GetDisk()),
		CreatedAt:  apiSnapshot.GetCreatedAt(),
		UpdatedAt:  apiSnapshot.GetUpdatedAt(),
	}

	// Handle optional fields using the Ok variants
	if orgID, ok := apiSnapshot.GetOrganizationIdOk(); ok && orgID != nil {
		snapshot.OrganizationID = *orgID
	}

	if imageName, ok := apiSnapshot.GetImageNameOk(); ok && imageName != nil {
		snapshot.ImageName = *imageName
	}

	if size, ok := apiSnapshot.GetSizeOk(); ok && size != nil {
		sizeVal := float64(*size)
		snapshot.Size = &sizeVal
	}

	if errorReason, ok := apiSnapshot.GetErrorReasonOk(); ok && errorReason != nil {
		snapshot.ErrorReason = errorReason
	}

	if lastUsedAt, ok := apiSnapshot.GetLastUsedAtOk(); ok && lastUsedAt != nil {
		snapshot.LastUsedAt = lastUsedAt
	}

	return snapshot
}
