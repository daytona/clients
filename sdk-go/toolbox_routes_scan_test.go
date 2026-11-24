// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"path/filepath"
	"strconv"
	"strings"
)

type sdkRoute struct {
	contract string
	method   string
	path     string
}

type sdkRouteScan struct {
	routes []sdkRoute
	seen   map[string]bool
	errors []string
}

func (s *sdkRouteScan) source(filename string, source []byte) {
	set := token.NewFileSet()
	file, err := parser.ParseFile(set, filename, source, 0)
	if err != nil {
		s.errors = append(s.errors, err.Error())
		return
	}
	transports := routeDirectTransports(file)
	// Include package-level declarations too, not only inline request literals.
	ast.Inspect(file, func(node ast.Node) bool {
		switch value := node.(type) {
		case *ast.CompositeLit:
			s.request(filename, value, transports[value])
		case *ast.AssignStmt:
			s.assignment(filename, value)
		}
		return true
	})
	for _, declaration := range file.Decls {
		function, ok := declaration.(*ast.FuncDecl)
		if !ok || function.Body == nil {
			continue
		}
		ast.Inspect(function.Body, func(node ast.Node) bool {
			if expression := routeHelperAnchor(function.Name.Name, node); expression != nil {
				s.anchor(filename, routeText(expression))
			}
			return true
		})
	}
}

func (s *sdkRouteScan) request(filename string, literal *ast.CompositeLit, transport string) {
	kind := routeText(literal.Type)
	if kind != "apiRequest" && kind != "toolboxRequest" && kind != "fileSigningRequest" {
		for _, element := range literal.Elts {
			field, ok := element.(*ast.KeyValueExpr)
			if !ok || routeText(field.Key) != "Path" {
				continue
			}
			if path, resolved := routeExpression(field.Value); resolved && strings.HasPrefix(path, "/") {
				s.errors = append(s.errors, filename+": unclassified Path literal: "+routeText(field))
			}
		}
		return
	}
	contract := "api"
	if kind != "apiRequest" {
		contract = "toolbox"
	}
	if transport != "" {
		contract = transport
	}
	method, path := "GET", ""
	for _, element := range literal.Elts {
		field, ok := element.(*ast.KeyValueExpr)
		if !ok {
			s.errors = append(s.errors, filename+": unkeyed request")
			return
		}
		switch routeText(field.Key) {
		case "Method", "method":
			method = routeMethod(field.Value)
		case "Path", "route":
			path = routeText(field.Value)
		}
	}
	key := filepath.Base(filename) + "|" + method + "|" + path
	if routes, ok := routeForwarders[key]; ok {
		s.seen[key] = true
		for _, route := range routes {
			// The request type, not the forwarding table, owns transport selection.
			route.contract = contract
			s.routes = append(s.routes, route)
		}
		return
	}
	expression, err := parser.ParseExpr(path)
	if err != nil {
		s.errors = append(s.errors, key+": unresolved request path")
		return
	}
	resolved, ok := routeExpression(expression)
	if !ok || !strings.HasPrefix(resolved, "/") {
		s.errors = append(s.errors, key+": unresolved request path")
		return
	}
	s.routes = append(s.routes, sdkRoute{contract, method, resolved})
}

func (s *sdkRouteScan) assignment(filename string, assignment *ast.AssignStmt) {
	for _, left := range assignment.Lhs {
		selector, ok := left.(*ast.SelectorExpr)
		if !ok {
			continue
		}
		switch selector.Sel.Name {
		case "Path", "Method":
			// These are git request-body fields, not HTTP request descriptions.
			if routeText(selector.X) == "body" {
				continue
			}
			s.anchor(filename, routeText(assignment))
			return
		case "path":
			if routeText(selector.X) == "prepared" {
				s.anchor(filename, routeText(assignment))
				return
			}
		}
	}
}

func (s *sdkRouteScan) anchor(filename, expression string) {
	key := filepath.Base(filename) + "|" + expression
	routes, ok := routeAnchors[key]
	if !ok {
		s.errors = append(s.errors, key+": unreviewed route builder")
		return
	}
	s.seen[key] = true
	s.routes = append(s.routes, routes...)
}

func routeExpression(expression ast.Expr) (string, bool) {
	switch value := expression.(type) {
	case *ast.BasicLit:
		if value.Kind == token.STRING {
			text, err := strconv.Unquote(value.Value)
			return text, err == nil
		}
	case *ast.ParenExpr:
		return routeExpression(value.X)
	case *ast.BinaryExpr:
		if value.Op == token.ADD {
			left, leftOK := routeExpression(value.X)
			right, rightOK := routeExpression(value.Y)
			return left + right, leftOK && rightOK
		}
	case *ast.Ident:
		// Only values known to be encoded path IDs may become parameters.
		switch value.Name {
		case "segment", "volumeID":
			return "{param}", true
		}
	case *ast.CallExpr:
		if routeText(value.Fun) == "strconv.Itoa" {
			return "{param}", true
		}
	}
	return "", false
}

func routeMethod(expression ast.Expr) string {
	text := routeText(expression)
	if strings.HasPrefix(text, "http.Method") {
		return strings.ToUpper(strings.TrimPrefix(text, "http.Method"))
	}
	method, err := strconv.Unquote(text)
	if err != nil {
		return text // An unknown method fails contract matching, never defaults to GET.
	}
	return method
}

func routeText(node ast.Node) string {
	var output bytes.Buffer
	if err := format.Node(&output, token.NewFileSet(), node); err != nil {
		return fmt.Sprintf("<invalid AST: %v>", err)
	}
	return output.String()
}
