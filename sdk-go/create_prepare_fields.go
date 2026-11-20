// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"maps"
	"strings"
	"time"
	"unicode/utf8"
)

func prepareCreateIdentity(body *createBody, opts CreateFromSnapshotOptions) error {
	if opts.Name != "" && (createTrim(opts.Name) == "" || !utf8.ValidString(opts.Name)) {
		return badRequest("name must be non-blank valid UTF-8 when given")
	}
	labels := maps.Clone(opts.Labels)
	if _, exists := labels[""]; exists {
		return badRequest("label keys must not be empty")
	}
	if opts.Name != "" {
		if name, exists := labels["name"]; exists && name != opts.Name {
			return badRequest("labels.name disagrees with name")
		}
		if labels == nil {
			labels = make(map[string]string)
		}
		labels["name"] = opts.Name
	}
	body.Name = opts.Name
	if labels != nil {
		body.Labels = &labels
	}
	switch strings.ToLower(createTrim(opts.Region)) {
	case "", "local":
	default:
		if err := headerText(opts.Region, "region"); err != nil {
			return err
		}
		body.Region = opts.Region
	}
	if opts.IdempotencyKey != "" {
		if err := headerText(opts.IdempotencyKey, "idempotencyKey"); err != nil {
			return err
		}
	}
	body.Key = opts.IdempotencyKey
	return nil
}

func createPorts(input []PortMapping) (*[]createPort, error) {
	if input == nil {
		return nil, nil //nolint:nilnil // A nil slice omits the optional wire field.
	}
	ports := make([]createPort, len(input))
	for index, port := range input {
		if port.Port < 1 || port.Port > 65535 {
			return nil, badRequest("ports[].port must be in 1..65535")
		}
		protocol := port.Protocol
		switch protocol {
		case "":
			protocol = "tcp"
		case "tcp", "udp":
		default:
			return nil, badRequest("ports[].protocol must be tcp or udp")
		}
		ports[index] = createPort{Port: port.Port, Protocol: protocol}
	}
	return &ports, nil
}

func createProvisionBudget(input *time.Duration) (*int64, error) {
	if input == nil {
		return nil, nil //nolint:nilnil // A nil duration selects the server default.
	}
	duration := *input
	seconds := int64(duration / time.Second)
	switch {
	case duration == WaitForever:
		seconds = -1
	case duration < 0 || duration%time.Second != 0:
		return nil, badRequest("createTimeout must be whole non-negative seconds or WaitForever")
	}
	return &seconds, nil
}
