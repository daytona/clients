// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"strconv"
)

func parseComputerUseA11yNodes(roots []JSONValue, maxDepth int) ([]ComputerUseAccessibilityNode, error) {
	type entry struct {
		wire  JSONValue
		depth int
		into  *ComputerUseAccessibilityNode
	}
	nodes := make([]ComputerUseAccessibilityNode, len(roots))
	stack := make([]entry, 0, len(roots))
	for i := len(roots) - 1; i >= 0; i-- {
		stack = append(stack, entry{wire: roots[i], into: &nodes[i]})
	}
	for len(stack) > 0 {
		current := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if current.depth > maxDepth {
			return nil, badRequest("computer-use accessibility response: a node is nested deeper than " +
				strconv.Itoa(maxDepth) + " levels")
		}
		wire, err := parseObject(current.wire, "computer-use accessibility node")
		if err != nil {
			return nil, err
		}
		node, err := parseComputerUseA11yNode(wire)
		if err != nil {
			return nil, err
		}
		children, err := field[[]JSONValue](wire, "children", false)
		if err != nil {
			return nil, err
		}
		if children != nil {
			node.Children = make([]ComputerUseAccessibilityNode, len(children))
		}
		*current.into = node
		for i := len(children) - 1; i >= 0; i-- {
			stack = append(stack, entry{wire: children[i], depth: current.depth + 1, into: &node.Children[i]})
		}
	}
	return nodes, nil
}

func parseComputerUseA11yNode(wire jsonObject) (ComputerUseAccessibilityNode, error) {
	var node ComputerUseAccessibilityNode
	var err error
	node.ID, err = field[string](wire, "id", true)
	if err != nil {
		return node, err
	}
	node.Role, err = field[string](wire, "role", true)
	if err != nil {
		return node, err
	}
	node.Name, err = field[string](wire, "name", true)
	if err != nil {
		return node, err
	}
	node.Description, err = field[*string](wire, "description", false)
	if err != nil {
		return node, err
	}
	node.Value, err = field[*string](wire, "value", false)
	if err != nil {
		return node, err
	}
	node.ValueTruncated, err = field[bool](wire, "valueTruncated", false)
	if err != nil {
		return node, err
	}
	node.States, err = computerUseA11yStrings(wire, "states", true)
	if err != nil {
		return node, err
	}
	node.Actions, err = computerUseA11yStrings(wire, "actions", false)
	if err != nil {
		return node, err
	}
	bounds, err := parseObject(wire["bounds"], "computer-use accessibility node bounds")
	if err != nil {
		return node, err
	}
	for _, part := range []struct {
		name string
		into *int
	}{
		{"x", &node.Bounds.X},
		{"y", &node.Bounds.Y},
		{"width", &node.Bounds.Width},
		{"height", &node.Bounds.Height},
	} {
		value, parseErr := integerField(bounds, part.name, true)
		if parseErr != nil {
			return node, parseErr
		}
		if value < math.MinInt32 || value > math.MaxInt32 {
			return node, badRequest("response bounds field " + part.name + " must be an int32")
		}
		*part.into = int(value) // Range checked at the response boundary.
	}
	return node, nil
}

func computerUseA11yStrings(wire jsonObject, name string, required bool) ([]string, error) {
	items, err := field[[]JSONValue](wire, name, required)
	if err != nil || items == nil {
		return nil, err
	}
	strings := make([]string, len(items))
	for i, item := range items {
		value, parseErr := field[string](jsonObject{name: item}, name, true)
		if parseErr != nil {
			return nil, parseErr
		}
		strings[i] = value
	}
	return strings, nil
}
