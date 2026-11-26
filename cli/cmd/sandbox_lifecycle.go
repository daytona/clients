// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
)

// lifecycleActionsFor lists the lifecycle-policy actions a sandbox accepts:
// delete everywhere, stop on persistent non-GPU sandboxes, and pause on
// persistent Linux sandboxes. Unknown classes leave support to the server.
func lifecycleActionsFor(class string, persistent bool) []string {
	switch class {
	case workload.ClassLinux:
		if persistent {
			return []string{"stop", "pause", "delete"}
		}
		return []string{"delete"}
	case workload.ClassMacOS:
		if persistent {
			return []string{"stop", "delete"}
		}
		return []string{"delete"}
	case "gpu":
		return []string{"delete"}
	}
	return nil
}

// validateLifecycleActions refuses a lifecycle action the sandbox cannot
// run, before any request is made.
func validateLifecycleActions(class string, persistent bool, policy gen.UpdateSandboxLifecyclePolicyRequest) error {
	allowed := lifecycleActionsFor(class, persistent)
	if allowed == nil {
		return nil
	}
	check := func(flag string, action *gen.LifecycleAction) error {
		if action == nil {
			return nil
		}
		for _, ok := range allowed {
			if string(*action) == ok {
				return nil
			}
		}
		label := "a " + class
		if class == workload.ClassMacOS || class == workload.ClassLinux {
			label = map[bool]string{true: "a persistent ", false: "an ephemeral "}[persistent] + class
		}
		e := cerr.Usage("%s %q is not available for %s sandbox (allowed: %s)", flag, string(*action), label, strings.Join(allowed, ", "))
		switch string(*action) {
		case "pause":
			return e.WithHint("the pause lifecycle action requires a persistent Linux sandbox (`sandbox create --persistent`)")
		case "stop":
			return e.WithHint("the stop lifecycle action requires a persistent non-GPU sandbox (`sandbox create --persistent` or `sandbox create-macos --persistent`)")
		}
		return e
	}
	if err := check("--on-lifecycle-timeout (on_timeout)", policy.OnTimeout); err != nil {
		return err
	}
	return check("--on-idle-timeout (on_idle_timeout)", policy.OnIdleTimeout)
}

// validateLifecycleBody checks the merged create body's lifecycle policy
// against the create surface. Snapshot persistence is inherited, so only its
// action vocabulary is checked locally.
func validateLifecycleBody(sf createSurface, body input.Body) error {
	persistent := sf.snapshot
	if v, ok := lookupField(body, "persistent"); ok && v != nil {
		var isBool bool
		persistent, isBool = v.(bool)
		if !isBool {
			return cerr.Usage("\"persistent\" must be a boolean")
		}
	}
	raw, ok := lookupField(body, "lifecycle_policy")
	if !ok || raw == nil {
		return nil
	}
	nested, ok := raw.(map[string]any)
	if !ok {
		if b, isBody := raw.(input.Body); isBody {
			nested = b
		} else {
			return cerr.Usage("\"lifecycle_policy\" must be an object")
		}
	}
	policy, err := workload.DecodeBody[gen.UpdateSandboxLifecyclePolicyRequest](input.Body(nested))
	if err != nil {
		return err
	}
	class := workload.ClassLinux
	if sf.macos {
		class = workload.ClassMacOS
	}
	return validateLifecycleActions(class, persistent, policy)
}

// lookupField reads a --file field; keys are exact (input.CheckKeys).
func lookupField(body input.Body, key string) (any, bool) {
	v, ok := body[key]
	return v, ok
}
