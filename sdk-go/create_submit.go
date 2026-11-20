// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"time"
)

func (c *Client) submitCreate(ctx context.Context, prepared preparedCreate) (*Sandbox, error) {
	prepared.body.Key = createIdempotencyKey(prepared.body.Name, prepared.body.Key)
	headers := make(http.Header)
	if prepared.body.Region != "" {
		headers.Set("X-Daytona-Region", prepared.body.Region)
	}
	var raw JSONValue
	err := c.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: prepared.path, Body: prepared.body, Result: &raw,
		Headers: headers, IdempotencyKey: prepared.body.Key,
		Timeout: createHoldTimeout(c.config.timeout, prepared.wait),
	})
	if err != nil {
		return nil, uncertainCreateError(ctx, err, prepared.body.Key)
	}
	sandbox, capSeconds, err := c.parseCreated(raw, prepared)
	if err != nil {
		return nil, err
	}
	if ctx.Err() != nil {
		return nil, transportError(ctx, ctx.Err())
	}
	if prepared.wait == NoWait || sandbox.State() == SandboxStateStarted {
		return sandbox, nil
	}
	budget := prepared.wait
	if budget != WaitForever {
		// Compare before conversion: a valid, very large cap must not overflow Duration.
		if capSeconds >= budget.Seconds() {
			return sandbox, nil
		}
		budget -= time.Duration(capSeconds * float64(time.Second))
	}
	err = sandbox.Wait(ctx, &WaitOptions{Until: []SandboxState{SandboxStateStarted}, Timeout: budget})
	if waitErr := acceptedWaitError(ctx, err); waitErr != nil {
		return nil, waitErr
	}
	return sandbox, nil
}

func createHoldTimeout(requestTimeout, wait time.Duration) time.Duration {
	if wait == NoWait || boundedTimeout(requestTimeout) == 0 {
		return 0
	}
	if wait == WaitForever {
		return NoTimeout
	}
	hold := time.Duration(wireSeconds(wait)) * time.Second
	return max(requestTimeout, hold+5*time.Second)
}

func uncertainCreateError(ctx context.Context, err error, key string) error {
	if ctx.Err() != nil {
		return transportError(ctx, ctx.Err())
	}
	var detail *Error
	if !errors.As(err, &detail) {
		return err
	}
	switch {
	case errors.Is(err, ErrClientTimeout):
		return &SandboxCreateRequestTimeoutError{SDKError: detail, IdempotencyKey: key}
	case errors.Is(err, ErrConnection):
		return &SandboxCreateConnectionError{SDKError: detail, IdempotencyKey: key}
	default:
		return err
	}
}

func acceptedWaitError(ctx context.Context, err error) error {
	if ctx.Err() != nil {
		return transportError(ctx, ctx.Err())
	}
	if err == nil {
		return nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, ErrRequestCancelled) {
		return err
	}
	var detail *Error
	if errors.As(err, &detail) {
		return nil
	}
	return err
}

func (c *Client) parseCreated(raw JSONValue, prepared preparedCreate) (*Sandbox, float64, error) {
	object, err := parseObject(raw, "sandbox create")
	if err != nil {
		return nil, 0, err
	}
	id, err := field[string](object, "id", true)
	if err != nil {
		return nil, 0, err
	}
	if _, pathErr := pathSegment(id); pathErr != nil {
		return nil, 0, badRequest("sandbox create response is missing the sandbox id")
	}
	status := createResponseText(object, "status")
	init := sandboxInit{
		Info: SandboxInfo{
			ID: id, Name: createResponseText(object, "name"),
			State: sandboxState(status), RawState: status,
			Class: prepared.class, RawClass: string(prepared.class), Region: prepared.body.Region,
			ReservationID: createResponseText(object, "reservation_id"),
		},
		ToolboxURL: createResponseText(object, "toolbox_proxy_url"), FromCreate: true,
	}
	if init.Info.Region == "" {
		init.Info.Region = regionOf(id)
	}
	if strings.HasPrefix(prepared.body.Image, "img_") {
		init.Info.Image = &ImageRef{ID: prepared.body.Image}
	}
	if strings.HasPrefix(prepared.body.Snapshot, "snap_") {
		init.Info.Snapshot = &SnapshotRef{ID: prepared.body.Snapshot}
	}
	if image, parseErr := parseObject(object["image"], "image"); parseErr == nil {
		if imageID := createResponseText(image, "id"); imageID != "" {
			init.Info.Image = &ImageRef{ID: imageID, Name: createResponseText(image, "name")}
		}
	}
	setCreateCredential(&init, object)
	waitCap, capErr := field[float64](object, "wait_cap_seconds", false)
	if capErr != nil || waitCap < 0 {
		waitCap = 0
	}
	sandbox, err := c.newSandbox(init)
	return sandbox, waitCap, err
}

func setCreateCredential(init *sandboxInit, object jsonObject) {
	token := createResponseText(object, "access_token")
	if headerText(token, "access_token") == nil {
		init.AccessToken = token
	}
	expiry := createResponseText(object, "access_token_expires_at")
	if parsed, err := parseTimestamp(expiry); err == nil {
		init.AccessTokenExpiresAt = parsed
	}
}

func createResponseText(object jsonObject, name string) string {
	// Once accepted, absent or mistyped optional response fields remain unknown.
	value, err := field[string](object, name, false)
	if err != nil {
		return ""
	}
	return value
}
