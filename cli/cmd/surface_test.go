// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// The committed public surface of the CLI: every command path with its
// aliases and hidden/deprecated state, every flag with its type, shorthand and
// default, and every environment variable the CLI reads. A change to any of
// them fails this test until the snapshot is regenerated, so it shows up in
// review; scripts/breaking/breaking_changes.py compares it with the last stable
// release. Line format: `<name>: <kind>[ <detail>]`, shared with the SDK
// snapshots.
var updateSurface = flag.Bool("update", false, "rewrite cli/api-surface/daytona.api.txt")

const (
	surfacePath       = "../api-surface/daytona.api.txt"
	surfaceRegenerate = "(cd cli && go test ./cmd -run TestPublicSurface -update)"
)

func TestPublicSurface(t *testing.T) {
	lines := commandSurface(NewRoot(io.Discard, io.Discard))
	env, err := envSurface("..")
	if err != nil {
		t.Fatal(err)
	}
	got := renderSurface(append(lines, env...))
	if *updateSurface {
		if err := os.MkdirAll(filepath.Dir(surfacePath), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(surfacePath, []byte(got), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(surfacePath)
	if err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	if string(want) == got {
		return
	}
	before := lineSet(string(want))
	after := lineSet(got)
	var diff strings.Builder
	for _, l := range strings.Split(string(want), "\n") {
		if l != "" && !after[l] {
			fmt.Fprintf(&diff, "  - %s\n", l)
		}
	}
	for _, l := range strings.Split(got, "\n") {
		if l != "" && !before[l] {
			fmt.Fprintf(&diff, "  + %s\n", l)
		}
	}
	t.Fatalf("cli/api-surface/daytona.api.txt is stale: the CLI's public surface changed.\n%sRegenerate with `%s` and commit the result.", diff.String(), surfaceRegenerate)
}

func lineSet(text string) map[string]bool {
	set := map[string]bool{}
	for _, l := range strings.Split(text, "\n") {
		set[l] = true
	}
	return set
}

func commandSurface(root *cobra.Command) []string {
	// Cobra adds `help` and `completion` when the tree executes; add them now
	// so they are part of the surface too.
	root.InitDefaultHelpCmd()
	root.InitDefaultCompletionCmd()
	var lines []string
	var walk func(c *cobra.Command)
	walk = func(c *cobra.Command) {
		path := c.CommandPath()
		kind := "command"
		if c.Deprecated != "" {
			kind = "deprecated " + kind
		}
		if c.Hidden {
			kind = "hidden " + kind
		}
		lines = append(lines, path+": "+kind)
		for _, alias := range c.Aliases {
			lines = append(lines, path+": alias "+alias)
		}
		c.LocalNonPersistentFlags().VisitAll(func(f *pflag.Flag) { lines = append(lines, flagLine(path, f, false)) })
		c.PersistentFlags().VisitAll(func(f *pflag.Flag) { lines = append(lines, flagLine(path, f, true)) })
		for _, sub := range c.Commands() {
			walk(sub)
		}
	}
	walk(root)
	return lines
}

func flagLine(path string, f *pflag.Flag, persistent bool) string {
	var kind []string
	if f.Hidden {
		kind = append(kind, "hidden")
	}
	if f.Deprecated != "" {
		kind = append(kind, "deprecated")
	}
	if persistent {
		kind = append(kind, "persistent")
	}
	kind = append(kind, "flag", f.Value.Type())
	if f.Shorthand != "" {
		short := "-" + f.Shorthand
		if f.ShorthandDeprecated != "" {
			short += " (deprecated)"
		}
		kind = append(kind, short)
	}
	if req, ok := f.Annotations[cobra.BashCompOneRequiredFlag]; ok && len(req) > 0 && req[0] == "true" {
		kind = append(kind, "required")
	}
	kind = append(kind, "default="+strconv.Quote(f.DefValue))
	return path + " --" + f.Name + ": " + strings.Join(kind, " ")
}

// envSurface lists the environment variables the CLI reads: the string
// argument of every Getenv / getenv / LookupEnv call in the module's
// non-test sources (generated code excluded). An argument that is not a
// string literal or a string constant fails, so no read goes unlisted.
func envSurface(moduleRoot string) ([]string, error) {
	fset := token.NewFileSet()
	var files []*ast.File
	err := filepath.WalkDir(moduleRoot, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if path != moduleRoot && (d.Name() == "gen" || d.Name() == "testdata" || strings.HasPrefix(d.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		files = append(files, f)
		return nil
	})
	if err != nil {
		return nil, err
	}
	consts := map[string]string{}
	for _, f := range files {
		for _, decl := range f.Decls {
			gen, ok := decl.(*ast.GenDecl)
			if !ok || gen.Tok != token.CONST {
				continue
			}
			for _, spec := range gen.Specs {
				vs := spec.(*ast.ValueSpec)
				for i, name := range vs.Names {
					if i < len(vs.Values) {
						if lit, ok := vs.Values[i].(*ast.BasicLit); ok && lit.Kind == token.STRING {
							consts[f.Name.Name+"."+name.Name], _ = strconv.Unquote(lit.Value)
						}
					}
				}
			}
		}
	}
	seen := map[string]bool{}
	var problems []string
	for _, f := range files {
		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok || len(call.Args) != 1 {
				return true
			}
			var name string
			switch fn := call.Fun.(type) {
			case *ast.Ident:
				name = fn.Name
			case *ast.SelectorExpr:
				name = fn.Sel.Name
			}
			if name != "Getenv" && name != "getenv" && name != "LookupEnv" {
				return true
			}
			var value string
			switch arg := call.Args[0].(type) {
			case *ast.BasicLit:
				value, _ = strconv.Unquote(arg.Value)
			case *ast.Ident:
				value = consts[f.Name.Name+"."+arg.Name]
			case *ast.SelectorExpr:
				if pkg, ok := arg.X.(*ast.Ident); ok {
					value = consts[pkg.Name+"."+arg.Sel.Name]
				}
			}
			if value == "" {
				problems = append(problems, fset.Position(call.Pos()).String())
				return true
			}
			seen[value] = true
			return true
		})
	}
	if len(problems) > 0 {
		sort.Strings(problems)
		return nil, fmt.Errorf("environment variable read with a name that is not a string constant (list it in the surface by using one): %s", strings.Join(problems, ", "))
	}
	lines := make([]string, 0, len(seen))
	for name := range seen {
		lines = append(lines, "$"+name+": env")
	}
	return lines, nil
}

func renderSurface(lines []string) string {
	sort.Slice(lines, func(i, j int) bool {
		ki, ri, _ := strings.Cut(lines[i], ": ")
		kj, rj, _ := strings.Cut(lines[j], ": ")
		if ki != kj {
			return ki < kj
		}
		return ri < rj
	})
	header := "# Public surface of the daytona CLI: commands, flags and the environment variables it reads.\n" +
		"# Generated by cli/cmd/surface_test.go; do not edit. Regenerate: " + surfaceRegenerate + ".\n"
	return header + strings.Join(lines, "\n") + "\n"
}
