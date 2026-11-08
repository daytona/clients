// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"strconv"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/input"
	"github.com/spf13/pflag"
)

// DecodeBody is input.Decode; kept as the workload package's name for it.
func DecodeBody[T any](body input.Body) (T, error) { return input.Decode[T](body) }

func NestedBody(body input.Body, key string) input.Body {
	if cur, ok := body[key].(input.Body); ok {
		return cur
	}
	if cur, ok := body[key].(map[string]any); ok {
		next := input.Body(cur)
		body[key] = next
		return next
	}
	next := input.Body{}
	body[key] = next
	return next
}

func SetIfChanged[T any](body input.Body, flags *pflag.FlagSet, flag, key string, value T) {
	if flags.Changed(flag) {
		body.Set(key, value)
	}
}

func SetNestedIfChanged[T any](body input.Body, flags *pflag.FlagSet, flag, parent, key string, value T) {
	if flags.Changed(flag) {
		NestedBody(body, parent).Set(key, value)
	}
}

func SetKVMapIfChanged(body input.Body, flags *pflag.FlagSet, flag, key, what string, values []string) error {
	if !flags.Changed(flag) {
		return nil
	}
	kv, err := input.KV(values, what)
	if err != nil {
		return err
	}
	body.Set(key, kv)
	return nil
}

func SetSliceIfChanged(body input.Body, flags *pflag.FlagSet, flag, key string, values []string) {
	if flags.Changed(flag) {
		body.Set(key, values)
	}
}

func ParsePorts(values []string) ([]gen.PortMapping, error) {
	ports := make([]gen.PortMapping, 0, len(values))
	for _, value := range values {
		parts := strings.SplitN(strings.TrimSpace(value), "/", 2)
		port, err := strconv.ParseInt(parts[0], 10, 32)
		if err != nil || port <= 0 || port > 65535 {
			return nil, cerr.Usage("--port wants PORT or PORT/PROTO, got %q", value)
		}
		item := gen.PortMapping{Port: int32(port)}
		if len(parts) == 2 {
			proto := strings.ToLower(strings.TrimSpace(parts[1]))
			if proto != "tcp" && proto != "udp" {
				return nil, cerr.Usage("--port wants PORT or PORT/{tcp|udp}, got %q", value)
			}
			item.Proto = &proto
		}
		ports = append(ports, item)
	}
	return ports, nil
}

func ParseVolumeMounts(values []string) ([]gen.VolumeMount, error) {
	mounts := make([]gen.VolumeMount, 0, len(values))
	for _, value := range values {
		parts := strings.SplitN(value, ":", 4)
		if len(parts) < 2 || strings.TrimSpace(parts[0]) == "" || strings.TrimSpace(parts[1]) == "" {
			return nil, cerr.Usage("--volume wants VOLUME:MOUNT_PATH[:SUBPATH][:ro], got %q", value)
		}
		mount := gen.VolumeMount{Volume: parts[0], MountPath: parts[1]}
		switch len(parts) {
		case 3:
			segment := strings.TrimSpace(parts[2])
			if segment == "" {
				return nil, cerr.Usage("--volume wants VOLUME:MOUNT_PATH[:SUBPATH][:ro], got %q", value)
			}
			if strings.EqualFold(segment, "ro") {
				readOnly := true
				mount.ReadOnly = &readOnly
				break
			}
			subpath := parts[2]
			mount.Subpath = &subpath
		case 4:
			if strings.TrimSpace(parts[2]) == "" || !strings.EqualFold(strings.TrimSpace(parts[3]), "ro") {
				return nil, cerr.Usage("--volume wants VOLUME:MOUNT_PATH[:SUBPATH][:ro], got %q", value)
			}
			subpath := parts[2]
			mount.Subpath = &subpath
			readOnly := true
			mount.ReadOnly = &readOnly
		}
		mounts = append(mounts, mount)
	}
	return mounts, nil
}
