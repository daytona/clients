// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// LifecyclePolicyOptions changes the supplied expiry settings. Zero fields are
// omitted. Durations must be whole seconds, at most 2,147,483,647 seconds.
// On creation an armed idle expiry and its action must be supplied together.
type LifecyclePolicyOptions struct {
	// Timeout is the lifetime budget. Positive values re-anchor the deadline;
	// the lifetime timeout cannot be disabled.
	Timeout time.Duration
	// OnTimeout is the lifetime action. Delete works for every class; Stop
	// requires persistent macOS. Pause is not a supported policy action.
	OnTimeout LifecycleAction
	// IdleExpiry is the inactivity budget. NoTimeout clears the idle pair.
	// The server raises positive budgets below one minute to one minute.
	IdleExpiry time.Duration
	// OnIdleTimeout selects the inactivity action, with OnTimeout's restrictions.
	OnIdleTimeout LifecycleAction
}

type lifecyclePolicyWire struct {
	TimeoutSeconds    *int64          `json:"timeout_seconds,omitempty"`
	OnTimeout         LifecycleAction `json:"on_timeout,omitempty"`
	IdleExpirySeconds *int64          `json:"idle_expiry_seconds,omitempty"`
	OnIdleTimeout     LifecycleAction `json:"on_idle_timeout,omitempty"`
}

func lifecyclePolicyBody(opts *LifecyclePolicyOptions, creating bool) (*lifecyclePolicyWire, error) {
	if opts == nil || *opts == (LifecyclePolicyOptions{}) {
		return nil, nil //nolint:nilnil // An empty policy is omitted on creation.
	}
	body := &lifecyclePolicyWire{OnTimeout: opts.OnTimeout, OnIdleTimeout: opts.OnIdleTimeout}
	var err error
	body.TimeoutSeconds, err = lifecyclePolicySeconds("timeout", opts.Timeout)
	if err != nil {
		return nil, err
	}
	body.IdleExpirySeconds, err = lifecyclePolicySeconds("idleExpiry", opts.IdleExpiry)
	if err != nil {
		return nil, err
	}
	for _, action := range []LifecycleAction{opts.OnTimeout, opts.OnIdleTimeout} {
		switch action {
		case "", LifecycleActionStop, LifecycleActionPause, LifecycleActionDelete:
		default:
			return nil, badRequest("lifecycle policy action must be stop, pause or delete")
		}
	}
	if opts.IdleExpiry == NoTimeout && opts.OnIdleTimeout != "" {
		return nil, badRequest("onIdleTimeout cannot be set while clearing idleExpiry")
	}
	if creating && (opts.IdleExpiry > 0) != (opts.OnIdleTimeout != "") {
		return nil, badRequest("idleExpiry and onIdleTimeout must be supplied together on creation")
	}
	return body, nil
}

func lifecyclePolicySeconds(name string, value time.Duration) (*int64, error) {
	if value == 0 {
		return nil, nil //nolint:nilnil // Zero omits an optional policy duration.
	}
	seconds := int64(value / time.Second)
	switch {
	case name == "idleExpiry" && value == NoTimeout:
		seconds = -1
	case value <= 0 || value%time.Second != 0 || seconds > 2147483647:
		return nil, badRequest("lifecycle policy " + name + " must be whole positive seconds up to 2147483647")
	}
	return &seconds, nil
}

func lifecyclePolicyClassSupport(opts *LifecyclePolicyOptions, info SandboxInfo) error {
	if opts == nil {
		return nil
	}
	for _, action := range []LifecycleAction{opts.OnTimeout, opts.OnIdleTimeout} {
		switch action {
		case "", LifecycleActionDelete:
		case LifecycleActionStop:
			if info.Class != SandboxClassMacOS || info.Persistent == nil || !*info.Persistent {
				return badRequest("lifecycle policy stop requires a persistent macOS sandbox")
			}
		case LifecycleActionPause:
			return badRequest("lifecycle policy pause is unsupported; use Pause instead")
		default:
			return badRequest("lifecycle policy action must be stop, pause or delete")
		}
	}
	return nil
}
