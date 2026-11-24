// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"go/ast"
	"strings"
)

// Request types are aliases in production. Prefer the actual transport when a
// request is passed inline; the spelling of an alias cannot override its owner.
// Forwarded requests retain the explicit apiRequest/toolboxRequest convention.
func routeDirectTransports(file *ast.File) map[*ast.CompositeLit]string {
	transports := make(map[*ast.CompositeLit]string)
	ast.Inspect(file, func(node ast.Node) bool {
		call, ok := node.(*ast.CallExpr)
		if !ok {
			return true
		}
		method, ok := call.Fun.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		switch method.Sel.Name {
		case "do", "stream", "makeRequest":
		default:
			return true
		}
		receiver := routeText(method.X)
		contract := ""
		switch {
		case strings.HasSuffix(receiver, ".api()"):
			contract = "api"
		case strings.HasSuffix(receiver, ".toolbox()"):
			contract = "toolbox"
		default:
			return true
		}
		for _, argument := range call.Args {
			if literal, inline := argument.(*ast.CompositeLit); inline {
				transports[literal] = contract
			}
		}
		return true
	})
	return transports
}

// Identify the literal-bearing inputs to the explicitly inventoried forwarders.
// Everything else resolves directly from a request's Path expression.
func routeHelperAnchor(function string, node ast.Node) ast.Node {
	switch value := node.(type) {
	case *ast.KeyValueExpr:
		if routeText(value.Key) == "path" {
			if path, ok := routeExpression(value.Value); ok && strings.HasPrefix(path, "/") {
				return value
			}
		}
	case *ast.BinaryExpr:
		if routeText(value.X) == "operation" {
			return value
		}
	case *ast.ReturnStmt:
		switch function {
		case "artifactPath", "portsPath", "portPath", "buildLogDiscoveryPath":
			if len(value.Results) > 0 {
				if _, ok := value.Results[0].(*ast.BinaryExpr); ok {
					return value.Results[0]
				}
			}
		}
	case *ast.CallExpr:
		switch routeText(value.Fun) {
		case "artifactPath", "buildLogDiscoveryPath":
			return value
		case "relativeURL":
			if function == "Attach" {
				return value
			}
		case "read":
			return value
		case "s.read":
			if len(value.Args) == 3 {
				return value
			}
		}
	case *ast.CompositeLit:
		switch routeText(value.Type) {
		case "artifactListReader", "buildLogSource", "lifecycleTransition":
			return value
		}
	case *ast.AssignStmt:
		if len(value.Lhs) == 1 && routeText(value.Lhs[0]) == "action" {
			return value
		}
		if len(value.Lhs) > 0 && routeText(value.Lhs[0]) == "path" && len(value.Rhs) > 0 {
			if path, ok := routeExpression(value.Rhs[0]); ok && strings.HasPrefix(path, "/") {
				return value
			}
		}
	}
	return nil
}
