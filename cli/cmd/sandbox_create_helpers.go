// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"math"
	"net/http"
	"os"
	"strings"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/daytona/clients/cli/internal/workload"
	"github.com/spf13/cobra"
)

// imageOptional: the surface boots the platform default image when the body
// names none (Linux `sandbox create`); a present but
// blank image stays a usage error.
func buildSandboxCreateBody(cmd *cobra.Command, a *app.App, flags sandboxCreateFlags, fromSnapshot, imageOptional bool) (input.Body, error) {
	body, err := input.Load(a.Globals.File, os.Stdin, false)
	if err != nil {
		return nil, err
	}
	if cmd.Flags().Changed("idempotency-key") {
		delete(body, "idempotency_key")
	}
	if _, ok := body["volumes"]; ok && cmd.Flags().Lookup("volume") == nil {
		return nil, cerr.Usage("volumes is not supported on this create command; remove it from --file")
	}
	if a.Globals.Region != "" {
		body.Set("region_id", a.Globals.Region)
	}
	workload.SetIfChanged(body, cmd.Flags(), "name", "name", flags.name)
	workload.SetIfChanged(body, cmd.Flags(), "cpu", "cpu", flags.cpu)
	workload.SetIfChanged(body, cmd.Flags(), "cores", "cores", flags.cores)
	workload.SetIfChanged(body, cmd.Flags(), "memory", "memory_mib", flags.memory)
	workload.SetIfChanged(body, cmd.Flags(), "disk", "disk_mib", flags.disk)
	workload.SetIfChanged(body, cmd.Flags(), "cpu-max", "cores_max", flags.cpuMax)
	workload.SetIfChanged(body, cmd.Flags(), "memory-max", "memory_max_mib", flags.memoryMax)
	workload.SetIfChanged(body, cmd.Flags(), "disk-max", "disk_max_mib", flags.diskMax)
	workload.SetIfChanged(body, cmd.Flags(), "cpu-arch", "cpu_arch", flags.cpuArch)
	workload.SetIfChanged(body, cmd.Flags(), "cpu-type", "cpu_type", flags.cpuType)
	workload.SetIfChanged(body, cmd.Flags(), "user", "user", flags.user)
	workload.SetIfChanged(body, cmd.Flags(), "persistent", "persistent", flags.persistent)
	workload.SetIfChanged(body, cmd.Flags(), "spot", "spot", flags.spot)
	workload.SetIfChanged(body, cmd.Flags(), "wait", "wait_seconds", flags.wait.seconds)
	workload.SetIfChanged(body, cmd.Flags(), "create-ttl", "create_timeout_seconds", flags.createTTL.seconds)
	workload.SetIfChanged(body, cmd.Flags(), "reservation", "reservation", flags.reservation)
	workload.SetIfChanged(body, cmd.Flags(), "auto-renew", "auto_renew", flags.autoRenew)
	if err := workload.SetKVMapIfChanged(body, cmd.Flags(), "env", "env", "--env", flags.env); err != nil {
		return nil, err
	}
	if err := workload.SetKVMapIfChanged(body, cmd.Flags(), "label", "labels", "--label", flags.labels); err != nil {
		return nil, err
	}
	workload.SetSliceIfChanged(body, cmd.Flags(), "secret", "secrets", flags.secrets)
	if cmd.Flags().Changed("port") {
		ports, err := workload.ParsePorts(flags.ports)
		if err != nil {
			return nil, err
		}
		body.Set("ports", ports)
	}
	if cmd.Flags().Changed("volume") {
		volumes, err := workload.ParseVolumeMounts(flags.volumes)
		if err != nil {
			return nil, err
		}
		body.Set("volumes", volumes)
	}
	if err := checkCPUMax(body); err != nil {
		return nil, err
	}
	if err := setNetworkPolicy(cmd, body, flags); err != nil {
		return nil, err
	}
	if cmd.Flags().Changed("lifecycle-timeout") {
		workload.NestedBody(body, "lifecycle_policy").Set("timeout_seconds", flags.lifecycleTTL.seconds)
	}
	if cmd.Flags().Changed("on-lifecycle-timeout") {
		workload.NestedBody(body, "lifecycle_policy").Set("on_timeout", flags.onLifecycleTTL)
	}
	if cmd.Flags().Changed("idle-timeout") {
		workload.NestedBody(body, "lifecycle_policy").Set("idle_expiry_seconds", flags.idleTimeout.seconds)
	}
	if cmd.Flags().Changed("on-idle-timeout") {
		workload.NestedBody(body, "lifecycle_policy").Set("on_idle_timeout", flags.onIdleTimeout)
	}
	if fromSnapshot {
		if _, ok := body["image"]; ok {
			return nil, cerr.Usage("a from-snapshot create has no `image`; remove it from --file")
		}
		body.Set("snapshot", flags.snapshot)
		return body, nil
	}
	if cmd.Flags().Changed("image") {
		body.Set("image", flags.image)
	}
	if imageOptional {
		if image, ok := body["image"]; ok && image == nil {
			delete(body, "image")
		}
		if _, ok := body["image"]; ok && !bootSourceSet(body["image"]) {
			if !cmd.Flags().Changed("image") {
				return nil, cerr.Usage("image must not be empty; omit it to boot the platform default image")
			}
			return nil, cerr.Usage("--image must not be empty; omit it to boot the platform default image")
		}
		return body, nil
	}
	if !bootSourceSet(body["image"]) {
		return nil, cerr.Usage("--image is required")
	}
	return body, nil
}

func sandboxCreateHeaders(flags sandboxCreateFlags, cmd *cobra.Command, region string, body input.Body) http.Header {
	headers := http.Header{}
	if cmd.Flags().Changed("idempotency-key") {
		headers.Set("Idempotency-Key", flags.idempotencyKey)
	} else if value, ok := body["idempotency_key"].(string); ok && strings.TrimSpace(value) != "" {
		headers.Set("Idempotency-Key", value)
	}
	if region != "" {
		headers.Set("x-daytona-region", region)
	}
	if len(headers) == 0 {
		return nil
	}
	return headers
}

func bootSourceSet(value any) bool {
	if value == nil {
		return false
	}
	if text, ok := value.(string); ok {
		return strings.TrimSpace(text) != ""
	}
	return true
}

// setNetworkPolicy maps the network-policy flags onto `network_policy`
// on Linux and macOS creates. A domain list implies default-deny: an
// omitted --block-all is left for the API to normalize to true, and an
// explicit --block-all=false beside --allow-domain is the same usage error the
// API answers with NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL.
func setNetworkPolicy(cmd *cobra.Command, body input.Body, flags sandboxCreateFlags) error {
	changed := cmd.Flags().Changed
	if !changed("allow-domain") && !changed("allow-cidr") && !changed("block-all") {
		return nil
	}
	if changed("allow-domain") && changed("block-all") && !flags.blockAll {
		return cerr.Usage("--allow-domain implies --block-all; --block-all=false is rejected (NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL)")
	}
	policy := workload.NestedBody(body, "network_policy")
	if changed("allow-domain") {
		policy.Set("allow_domains", flags.allowDomains)
	}
	if changed("allow-cidr") {
		policy.Set("allow_cidrs", flags.allowCIDRs)
	}
	if changed("block-all") {
		policy.Set("block_all", flags.blockAll)
	}
	// Judged on the merged policy (flags over --file): without a domain list
	// the API leaves block_all false, and an open policy ignores allow_cidrs,
	// so the sandbox would be created unrestricted while the command line
	// reads like an allow list.
	if changed("allow-cidr") && policy["block_all"] != true && policy["allow_domains"] == nil {
		return cerr.Usage("--allow-cidr restricts egress only with --block-all or --allow-domain; without them the policy is open")
	}
	return nil
}

// checkCPUMax refuses a CPU limit that is not a positive multiple of 0.5
// cores (from --cpu-max or the file's cores_max) before any request.
func checkCPUMax(body input.Body) error {
	raw, ok := lookupField(body, "cores_max")
	if !ok || raw == nil {
		return nil
	}
	var v float64
	switch n := raw.(type) {
	case float64:
		v = n
	case json.Number:
		f, err := n.Float64()
		if err != nil {
			return cerr.Usage("cores_max must be a number")
		}
		v = f
	default:
		return nil // the typed decode reports a wrong type
	}
	if v <= 0 || math.Mod(v*2, 1) != 0 {
		return cerr.Usage("--cpu-max (cores_max) must be a positive multiple of 0.5 cores, got %v", v)
	}
	return nil
}
