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
		// The positional arguments, as the usage line spells them, and the
		// argument counts the command's validator accepts.
		_, args, _ := strings.Cut(c.Use, " ")
		lines = append(lines, strings.TrimSpace(path+": usage "+strings.TrimSpace(args)))
		lines = append(lines, path+": args "+acceptedArgs(c))
		lines = append(lines, flagGroups(c)...)
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

// argsProbed matches ARGS_PROBED in scripts/breaking/breaking_changes.py.
const argsProbed = 16

// argsUnbounded stands for "any number": a validator that also accepts this
// many arguments has no upper bound the CLI would reach.
const argsUnbounded = 4096

// acceptedArgs probes the command's argument validator with 0..argsProbed
// arguments and spells the counts it accepts: "1", "0-2", "1+" (no upper
// bound), "none". A count range that reaches argsProbed is written "N+" only
// when the validator also accepts argsUnbounded arguments, so a new upper
// bound shows up.
func acceptedArgs(c *cobra.Command) string {
	var accepted []int
	for n := 0; n <= argsProbed; n++ {
		if c.ValidateArgs(make([]string, n)) == nil {
			accepted = append(accepted, n)
		}
	}
	unbounded := c.ValidateArgs(make([]string, argsUnbounded)) == nil
	if len(accepted) == 0 {
		return "none"
	}
	var parts []string
	for i := 0; i < len(accepted); {
		j := i
		for j+1 < len(accepted) && accepted[j+1] == accepted[j]+1 {
			j++
		}
		switch {
		case accepted[j] == argsProbed && unbounded:
			parts = append(parts, strconv.Itoa(accepted[i])+"+")
		case i == j:
			parts = append(parts, strconv.Itoa(accepted[i]))
		default:
			parts = append(parts, strconv.Itoa(accepted[i])+"-"+strconv.Itoa(accepted[j]))
		}
		i = j + 1
	}
	return strings.Join(parts, ",")
}

// Cobra's flag-group annotations (MarkFlagsMutuallyExclusive,
// MarkFlagsOneRequired, MarkFlagsRequiredTogether).
var flagGroupKinds = map[string]string{
	"cobra_annotation_mutually_exclusive":     "mutually-exclusive",
	"cobra_annotation_one_required":           "one-required",
	"cobra_annotation_required_if_others_set": "required-together",
}

// flagGroups lists the flag groups cobra enforces on the command, one line
// each: `<path>: flag-group <kind> <flag> <flag>...`. Cobra stores a group on
// its flags and enforces it on every command that has all of them, local or
// inherited, so a group of persistent flags is listed under each command
// that inherits them.
func flagGroups(c *cobra.Command) []string {
	seen := map[string]bool{}
	var lines []string
	visible := map[string]bool{}
	var all []*pflag.Flag
	for _, set := range []*pflag.FlagSet{c.LocalFlags(), c.InheritedFlags()} {
		set.VisitAll(func(f *pflag.Flag) {
			if !visible[f.Name] {
				visible[f.Name] = true
				all = append(all, f)
			}
		})
	}
	for _, f := range all {
		for annotation, kind := range flagGroupKinds {
			for _, group := range f.Annotations[annotation] {
				names := strings.Fields(group)
				enforced := true
				for _, name := range names {
					enforced = enforced && visible[name]
				}
				if !enforced {
					continue
				}
				sort.Strings(names)
				line := c.CommandPath() + ": flag-group " + kind + " " + strings.Join(names, " ")
				if !seen[line] {
					seen[line] = true
					lines = append(lines, line)
				}
			}
		}
	}
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

// envSurface lists the environment variables the CLI reads, from the
// module's non-test sources (generated code excluded): the string argument of
// every call through os.Getenv / os.LookupEnv or through a name that holds
// one. A reader name is any variable or field assigned os.Getenv /
// os.LookupEnv or another reader, and any parameter of a module function that
// a reader is passed to. An argument that is not a string literal or constant
// fails, so no read goes unlisted.
func envSurface(moduleRoot string) ([]string, error) {
	fset := token.NewFileSet()
	var files []*ast.File
	// Packages go by import path (module path + directory), never by the
	// last path element: a package name may differ from its directory.
	modulePath := "local"
	if mod, err := os.ReadFile(filepath.Join(moduleRoot, "go.mod")); err == nil {
		for _, line := range strings.Split(string(mod), "\n") {
			if rest, ok := strings.CutPrefix(strings.TrimSpace(line), "module "); ok {
				modulePath = strings.TrimSpace(rest)
				break
			}
		}
	}
	pkgPath := map[*ast.File]string{}
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
		rel, err := filepath.Rel(moduleRoot, filepath.Dir(path))
		if err != nil {
			return err
		}
		pkgPath[f] = modulePath
		if rel != "." {
			pkgPath[f] = modulePath + "/" + filepath.ToSlash(rel)
		}
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
				vs, ok := spec.(*ast.ValueSpec)
				if !ok {
					continue
				}
				for i, name := range vs.Names {
					if i < len(vs.Values) {
						if lit, ok := vs.Values[i].(*ast.BasicLit); ok && lit.Kind == token.STRING {
							consts[pkgPath[f]+"."+name.Name], _ = strconv.Unquote(lit.Value)
						}
					}
				}
			}
		}
	}

	// Parameter lists by declaration: "import/path.Func" for a function, ".Method"
	// for every method of that name (a method call does not say its
	// receiver's type, so all of them are candidates).
	params := map[string][][]string{}
	for _, f := range files {
		for _, decl := range f.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok {
				continue
			}
			var names []string
			for _, field := range fn.Type.Params.List {
				if len(field.Names) == 0 {
					names = append(names, "_")
				}
				for _, name := range field.Names {
					names = append(names, name.Name)
				}
			}
			key := pkgPath[f] + "." + fn.Name.Name
			if fn.Recv != nil {
				key = "." + fn.Name.Name
			}
			params[key] = append(params[key], names)
		}
	}
	// The import path each file's import names refer to. A module package is
	// named by its package clause; another one by its last path element.
	pkgName := map[string]string{}
	for f, path := range pkgPath {
		pkgName[path] = f.Name.Name
	}
	imports := map[*ast.File]map[string]string{}
	for _, f := range files {
		local := map[string]string{}
		for _, spec := range f.Imports {
			path, _ := strconv.Unquote(spec.Path.Value)
			name, ok := pkgName[path]
			if !ok {
				name = path[strings.LastIndex(path, "/")+1:]
			}
			if spec.Name != nil {
				name = spec.Name.Name
			}
			local[name] = path
		}
		imports[f] = local
	}
	callees := func(f *ast.File, fun ast.Expr) [][]string {
		switch x := fun.(type) {
		case *ast.Ident:
			return params[pkgPath[f]+"."+x.Name]
		case *ast.SelectorExpr:
			if pkg, ok := x.X.(*ast.Ident); ok {
				if path, ok := imports[f][pkg.Name]; ok {
					return params[path+"."+x.Sel.Name]
				}
			}
			return params["."+x.Sel.Name]
		}
		return nil
	}
	readers := map[string]bool{}
	// os.Getenv / os.LookupEnv under whatever name the file imports "os" as.
	isOSReader := func(f *ast.File, e ast.Expr) bool {
		sel, ok := e.(*ast.SelectorExpr)
		if !ok {
			return false
		}
		pkg, ok := sel.X.(*ast.Ident)
		return ok && imports[f][pkg.Name] == "os" && (sel.Sel.Name == "Getenv" || sel.Sel.Name == "LookupEnv")
	}
	isReader := func(f *ast.File, e ast.Expr) bool {
		switch x := e.(type) {
		case *ast.Ident:
			return readers[x.Name]
		case *ast.SelectorExpr:
			return isOSReader(f, x) || readers[x.Sel.Name]
		}
		return false
	}
	nameOf := func(e ast.Expr) string {
		switch x := e.(type) {
		case *ast.Ident:
			return x.Name
		case *ast.SelectorExpr:
			return x.Sel.Name
		}
		return ""
	}
	add := func(name string) bool {
		if name == "" || name == "_" || readers[name] {
			return false
		}
		readers[name] = true
		return true
	}
	for changed := true; changed; {
		changed = false
		for _, f := range files {
			ast.Inspect(f, func(n ast.Node) bool {
				switch x := n.(type) {
				case *ast.CallExpr:
					for i, arg := range x.Args {
						if !isReader(f, arg) {
							continue
						}
						for _, names := range callees(f, x.Fun) {
							if i < len(names) {
								changed = add(names[i]) || changed
							}
						}
					}
				case *ast.AssignStmt:
					for i, rhs := range x.Rhs {
						if i < len(x.Lhs) && isReader(f, rhs) {
							changed = add(nameOf(x.Lhs[i])) || changed
						}
					}
				case *ast.ValueSpec:
					for i, value := range x.Values {
						if i < len(x.Names) && isReader(f, value) {
							changed = add(x.Names[i].Name) || changed
						}
					}
				case *ast.KeyValueExpr:
					if isReader(f, x.Value) {
						changed = add(nameOf(x.Key)) || changed
					}
				}
				return true
			})
		}
	}

	seen := map[string]bool{}
	var problems []string
	for _, f := range files {
		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok || !isReader(f, call.Fun) {
				return true
			}
			var value string
			if len(call.Args) == 1 {
				switch arg := call.Args[0].(type) {
				case *ast.BasicLit:
					value, _ = strconv.Unquote(arg.Value)
				case *ast.Ident:
					value = consts[pkgPath[f]+"."+arg.Name]
				case *ast.SelectorExpr:
					if pkg, ok := arg.X.(*ast.Ident); ok {
						value = consts[imports[f][pkg.Name]+"."+arg.Sel.Name]
					}
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

func TestEnvSurfaceFollowsReaders(t *testing.T) {
	write := func(t *testing.T, src string) string {
		t.Helper()
		dir := t.TempDir()
		if err := os.WriteFile(filepath.Join(dir, "x.go"), []byte(src), 0o644); err != nil {
			t.Fatal(err)
		}
		return dir
	}
	dir := write(t, `package x

import (
	"os"
	operating "os"
)

const name = "VIA_CONST"

type opts struct{ Lookup func(string) string }

func helper(get func(string) (string, bool)) { get("VIA_PARAM") }

type other struct{}

// A method named like the function above, its reader in another position:
// the call below binds os.Getenv to the method's parameter.
func (other) helper(n int, lookup func(string) string) { lookup("VIA_METHOD") }

func two(_ int, get func(string) string) { get("VIA_SECOND") }

func f() {
	os.Getenv("DIRECT")
	operating.Getenv("VIA_RENAMED_OS")
	read := os.Getenv
	read("VIA_ALIAS")
	again := read
	again(name)
	o := opts{Lookup: os.Getenv}
	o.Lookup("VIA_FIELD")
	helper(os.LookupEnv)
	other{}.helper(1, os.Getenv)
	two(0, os.Getenv)
}
`)
	got, err := envSurface(dir)
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(got)
	want := []string{"$DIRECT: env", "$VIA_ALIAS: env", "$VIA_CONST: env", "$VIA_FIELD: env", "$VIA_METHOD: env", "$VIA_PARAM: env", "$VIA_RENAMED_OS: env", "$VIA_SECOND: env"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("got %v, want %v", got, want)
	}

	// An imported package whose name differs from its directory.
	mod := t.TempDir()
	files := map[string]string{
		"go.mod":                "module example.com/m\n",
		"main.go":               "package main\n\nimport (\n\t\"os\"\n\n\t\"example.com/m/internal/v2env\"\n)\n\nfunc main() { env.Read(os.Getenv) }\n",
		"internal/v2env/env.go": "package env\n\nfunc Read(get func(string) string) { get(\"VIA_RENAMED_PACKAGE\") }\n",
	}
	for name, src := range files {
		if err := os.MkdirAll(filepath.Dir(filepath.Join(mod, name)), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(mod, name), []byte(src), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	got, err = envSurface(mod)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(got, ",") != "$VIA_RENAMED_PACKAGE: env" {
		t.Fatalf("got %v", got)
	}

	dynamic := write(t, `package x

import "os"

func f(key string) { read := os.Getenv; read(key) }
`)
	if _, err := envSurface(dynamic); err == nil || !strings.Contains(err.Error(), "not a string constant") {
		t.Fatalf("a read with a dynamic name must fail, got %v", err)
	}
}

func TestCommandSurfaceRecordsArgumentBoundsAndFlagGroups(t *testing.T) {
	root := &cobra.Command{Use: "x"}
	arbitrary := &cobra.Command{Use: "any [args]...", Args: cobra.ArbitraryArgs, Run: func(*cobra.Command, []string) {}}
	six := &cobra.Command{Use: "six [args]...", Args: cobra.RangeArgs(0, 6), Run: func(*cobra.Command, []string) {}}
	grouped := &cobra.Command{Use: "grouped", Run: func(*cobra.Command, []string) {}}
	grouped.Flags().Bool("json", false, "")
	grouped.Flags().Bool("yaml", false, "")
	grouped.MarkFlagsMutuallyExclusive("yaml", "json")
	root.AddCommand(arbitrary, six, grouped)
	// A group of the root's persistent flags, defined from a child: cobra
	// enforces it on every command that has both flags.
	root.PersistentFlags().Bool("quiet", false, "")
	root.PersistentFlags().Bool("debug", false, "")
	six.MarkFlagsMutuallyExclusive("quiet", "debug")
	lines := strings.Join(commandSurface(root), "\n")
	for _, want := range []string{
		"x any: args 0+", "x six: args 0-6", "x grouped: flag-group mutually-exclusive json yaml",
		"x six: flag-group mutually-exclusive debug quiet", "x any: flag-group mutually-exclusive debug quiet",
		"x: flag-group mutually-exclusive debug quiet",
	} {
		if !strings.Contains(lines, want) {
			t.Errorf("missing %q in:\n%s", want, lines)
		}
	}
}
