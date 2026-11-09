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
// `delete` everywhere, `stop` on a persistent macOS sandbox only. Pausing is
// the explicit `sandbox pause` command, never a lifecycle action. An unknown
// class returns nil: the server decides.
func lifecycleActionsFor(class string, persistent bool) []string {
	switch class {
	case workload.ClassLinux:
		return []string{"delete"}
	case workload.ClassMacOS:
		if persistent {
			return []string{"stop", "delete"}
		}
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
		if class == workload.ClassMacOS {
			label = map[bool]string{true: "a persistent macos", false: "an ephemeral macos"}[persistent]
		}
		e := cerr.Usage("%s %q is not available for %s sandbox (allowed: %s)", flag, string(*action), label, strings.Join(allowed, ", "))
		switch string(*action) {
		case "pause":
			return e.WithHint("pause a sandbox with `daytona sandbox pause`; lifecycle policies stop or delete")
		case "stop":
			return e.WithHint("only a persistent macOS sandbox (`sandbox create-macos --persistent`) can be stopped by its lifecycle policy")
		}
		return e
	}
	if err := check("--on-lifecycle-timeout (on_timeout)", policy.OnTimeout); err != nil {
		return err
	}
	return check("--on-idle-timeout (on_idle_timeout)", policy.OnIdleTimeout)
}

// validateLifecycleBody checks the merged create body's lifecycle policy
// against the class the create surface makes (persistence from the body).
func validateLifecycleBody(macos bool, body input.Body) error {
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
	persistent := false
	if macos {
		class = workload.ClassMacOS
		if v, ok := lookupField(body, "persistent"); ok {
			persistent, _ = v.(bool)
		}
	}
	return validateLifecycleActions(class, persistent, policy)
}

// lookupField reads a --file field; keys are exact (input.CheckKeys).
func lookupField(body input.Body, key string) (any, bool) {
	v, ok := body[key]
	return v, ok
}
