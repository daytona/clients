// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"math"
	"strconv"
)

// Ports inspects listeners and manages a sandbox's preview endpoints.
// Obtain it from Sandbox.Ports. It is safe for concurrent use.
type Ports struct{ sandbox *Sandbox }

// Ports returns the sandbox's port inspection and preview service.
func (s *Sandbox) Ports() *Ports { return &Ports{sandbox: s} }

// List returns every listening TCP port in one non-pageable page.
// The toolbox reports int32 values, without a port-range constraint.
func (p *Ports) List(ctx context.Context) (*Page[int], error) {
	var raw JSONValue
	if err := p.sandbox.toolbox().do(ctx, toolboxRequest{Path: "/port", Result: &raw}); err != nil {
		return nil, err
	}
	object, err := parseObject(raw, "port list")
	if err != nil {
		return nil, err
	}
	rows, err := field[[]JSONValue](object, "ports", true)
	if err != nil {
		return nil, err
	}
	items := make([]int, 0, len(rows))
	for _, row := range rows {
		port, parseErr := integerField(jsonObject{"ports": row}, "ports", true)
		if parseErr != nil {
			return nil, parseErr
		}
		if port < math.MinInt32 || port > math.MaxInt32 {
			return nil, badRequest("response field ports must contain int32 values")
		}
		items = append(items, int(port))
	}
	return &Page[int]{Items: items}, nil
}

// IsInUse reports whether port has a listener. Values outside 1..65535 return
// ErrBadRequest locally. The toolbox only supports 3000..9999 and returns
// BAD_REQUEST for other ports, rather than reporting false.
func (p *Ports) IsInUse(ctx context.Context, port int) (bool, error) {
	if err := validatePort(port); err != nil {
		return false, err
	}
	var raw JSONValue
	if err := p.sandbox.toolbox().do(ctx, toolboxRequest{
		Path: "/port/" + strconv.Itoa(port) + "/in-use", Result: &raw,
	}); err != nil {
		return false, err
	}
	object, err := parseObject(raw, "port in use")
	if err != nil {
		return false, err
	}
	return field[bool](object, "isInUse", true)
}

func validatePort(port int) error {
	if port < 1 || port > 65535 {
		return badRequest("port must be an integer in 1..65535")
	}
	return nil
}

func validatePreviewPort(port int) error {
	if err := validatePort(port); err != nil {
		return err
	}
	switch port {
	case 2280, 2281, 22220:
		return badRequest("port is reserved for the sandbox daemon")
	}
	return nil
}

func (p *Ports) portsPath() (string, error) {
	segment, err := pathSegment(p.sandbox.id)
	if err != nil {
		return "", err
	}
	return "/sandboxes/" + segment + "/ports", nil
}

func (p *Ports) portPath(port int) (string, error) {
	base, err := p.portsPath()
	if err != nil {
		return "", err
	}
	return base + "/" + strconv.Itoa(port), nil
}
