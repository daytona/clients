// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// UpdateLifecyclePolicy changes supplied policy settings and caches the echoed
// policy. Nil or empty options return ErrBadRequest. Unknown classes return
// ErrUnsupportedOperation. Deleted sandboxes return ErrConflict. Actions involving
// deletion additionally require delete:sandboxes permission.
func (s *Sandbox) UpdateLifecyclePolicy(ctx context.Context, opts *LifecyclePolicyOptions) (*LifecyclePolicy, error) {
	body, err := lifecyclePolicyBody(opts, false)
	if err != nil {
		return nil, err
	}
	if body == nil {
		return nil, badRequest("UpdateLifecyclePolicy requires at least one lifecycle field")
	}
	info := s.Info()
	if info.Class == "" || (info.Class == SandboxClassUnknown && info.RawClass == "") ||
		(info.Class == SandboxClassMacOS && info.Persistent == nil) {
		if refreshErr := s.Refresh(ctx); refreshErr != nil {
			return nil, refreshErr
		}
		info = s.Info()
	}
	switch info.Class {
	case SandboxClassLinux, SandboxClassMacOS:
	case SandboxClassUnknown:
		return nil, newSDKError(CodeUnsupportedOperation, 400, "unsupported sandbox class "+info.RawClass)
	default:
		return nil, newSDKError(CodeUnsupportedOperation, 400, "unsupported sandbox class "+info.RawClass)
	}
	if classErr := lifecyclePolicyClassSupport(opts, info); classErr != nil {
		return nil, classErr
	}
	policy, err := s.lifecyclePolicyRequest(ctx, apiRequest{
		Method: http.MethodPatch, Path: "/lifecycle-policy", Body: body,
	})
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.info.LifecyclePolicy = cloneLifecyclePolicy(policy)
	s.mu.Unlock()
	return policy, nil
}

// ResetIdleTimer records activity without changing the wall-clock timeout and
// returns the server's full policy. Older concurrent echoes never move the
// cached activity clock backwards. Deleting or deleted sandboxes return ErrConflict.
func (s *Sandbox) ResetIdleTimer(ctx context.Context) (*LifecyclePolicy, error) {
	policy, err := s.lifecyclePolicyRequest(ctx, apiRequest{
		Method: http.MethodPost, Path: "/reset-idle-timer",
	})
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	cached := s.info.LifecyclePolicy
	if cached == nil || cached.LastActivityAt.IsZero() ||
		(!policy.LastActivityAt.IsZero() && !policy.LastActivityAt.Before(cached.LastActivityAt)) {
		s.info.LifecyclePolicy = cloneLifecyclePolicy(policy)
	}
	s.mu.Unlock()
	return policy, nil
}

func (s *Sandbox) lifecyclePolicyRequest(ctx context.Context, request apiRequest) (*LifecyclePolicy, error) {
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	request.Path, request.Result = "/sandboxes/"+segment+request.Path, &raw
	if requestErr := s.client.api().do(ctx, request); requestErr != nil {
		return nil, requestErr
	}
	return parseLifecycleEcho(raw)
}

func cloneLifecyclePolicy(policy *LifecyclePolicy) *LifecyclePolicy {
	result := *policy
	result.Timeout = clonePointer(policy.Timeout)
	result.IdleExpiry = clonePointer(policy.IdleExpiry)
	return &result
}

func parseLifecycleEcho(raw JSONValue) (*LifecyclePolicy, error) {
	object, err := parseObject(raw, "sandbox lifecycle policy")
	if err != nil {
		return nil, err
	}
	if _, idErr := field[string](object, "id", true); idErr != nil {
		return nil, idErr
	}
	policy, err := field[jsonObject](object, "lifecycle_policy", true)
	if err != nil {
		return nil, err
	}
	for _, name := range []string{"timeout_seconds", "idle_expiry_seconds"} {
		value, fieldErr := integerField(policy, name, false)
		if fieldErr != nil {
			return nil, fieldErr
		}
		if value < -2147483648 || value > 2147483647 {
			return nil, badRequest("lifecycle_policy." + name + " must be an int32")
		}
	}
	return parseLifecycle(object["lifecycle_policy"])
}
