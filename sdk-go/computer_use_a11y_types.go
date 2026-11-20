// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// ComputerUseA11yScope selects which applications to inspect.
type ComputerUseA11yScope string

const (
	// ComputerUseA11yScopeUnknown represents an unrecognized scope.
	ComputerUseA11yScopeUnknown ComputerUseA11yScope = "unknown"
	// ComputerUseA11yScopeFocused inspects the focused application.
	ComputerUseA11yScopeFocused ComputerUseA11yScope = "focused"
	// ComputerUseA11yScopePID inspects the application selected by PID.
	ComputerUseA11yScopePID ComputerUseA11yScope = "pid"
	// ComputerUseA11yScopeAll inspects all applications.
	ComputerUseA11yScopeAll ComputerUseA11yScope = "all"
)

// ComputerUseA11yNameMatch selects how a node name is matched.
type ComputerUseA11yNameMatch string

const (
	// ComputerUseA11yNameMatchUnknown represents an unrecognized matching mode.
	ComputerUseA11yNameMatchUnknown ComputerUseA11yNameMatch = "unknown"
	// ComputerUseA11yNameMatchExact matches the complete name.
	ComputerUseA11yNameMatchExact ComputerUseA11yNameMatch = "exact"
	// ComputerUseA11yNameMatchSubstring matches part of the name.
	ComputerUseA11yNameMatchSubstring ComputerUseA11yNameMatch = "substring"
	// ComputerUseA11yNameMatchRegex matches a regular expression.
	ComputerUseA11yNameMatchRegex ComputerUseA11yNameMatch = "regex"
)

// ComputerUseA11yTreeOptions configures accessibility tree retrieval.
type ComputerUseA11yTreeOptions struct {
	// PID selects a process when scope is ComputerUseA11yScopePID.
	PID *int
	// MaxDepth counts levels below the root, from 0 to 256; nil sends 256.
	MaxDepth *int
}

// ComputerUseA11yFindOptions filters accessibility nodes. Nil fields are omitted.
// The sandbox validates filters and chooses omitted defaults.
type ComputerUseA11yFindOptions struct {
	// PID selects a process when scope is ComputerUseA11yScopePID.
	PID *int `json:"pid,omitempty"`
	// Role restricts the accessibility role; nil omits it.
	Role *string `json:"role,omitempty"`
	// Name restricts the accessible name; nil omits it.
	Name *string `json:"name,omitempty"`
	// NameMatch selects name matching; empty uses the sandbox default.
	NameMatch ComputerUseA11yNameMatch `json:"nameMatch,omitempty"`
	// States restricts node states; nil omits it, an empty slice sends [].
	States []string `json:"states,omitempty"`
	// Limit bounds matches; nil uses the sandbox default.
	Limit *int `json:"limit,omitempty"`
}

// ComputerUseA11yInvokeOptions selects an accessibility action.
type ComputerUseA11yInvokeOptions struct {
	// Action selects an action; nil invokes the node's default action.
	Action *string
}

// ComputerUseAccessibilityBounds is an accessibility node's rectangle in pixels.
// Coordinates and dimensions are signed 32-bit integers.
type ComputerUseAccessibilityBounds struct {
	// X is the horizontal coordinate in pixels.
	X int32
	// Y is the vertical coordinate in pixels.
	Y int32
	// Width is the width in pixels.
	Width int32
	// Height is the height in pixels.
	Height int32
}

// ComputerUseAccessibilityNode describes one accessibility control.
type ComputerUseAccessibilityNode struct {
	// ID identifies the node for subsequent actions.
	ID string
	// Role is the platform-defined accessibility role.
	Role string
	// Name is the accessible name.
	Name string
	// Description is nil when not reported.
	Description *string
	// Value is the current control value, never reported for password fields.
	Value *string
	// ValueTruncated reports a platform-truncated value; absent means false.
	ValueTruncated bool
	// Bounds is the control rectangle in pixels.
	Bounds ComputerUseAccessibilityBounds
	// States contains platform-defined accessibility states.
	States []string
	// Actions lists supported actions; nil means not reported.
	Actions []string
	// Children preserves server order; nil means not reported.
	Children []ComputerUseAccessibilityNode
}

// ComputerUseA11yTreeResponse contains an accessibility tree.
type ComputerUseA11yTreeResponse struct {
	// Root is the tree's root node.
	Root ComputerUseAccessibilityNode
	// Truncated reports that the sandbox limited the tree.
	Truncated bool
}

// ComputerUseA11yFindResponse contains matching accessibility nodes.
type ComputerUseA11yFindResponse struct {
	// Matches preserves server order.
	Matches []ComputerUseAccessibilityNode
	// Truncated reports that the sandbox limited the matches.
	Truncated bool
}
