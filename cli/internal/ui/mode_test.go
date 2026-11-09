// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui

import "testing"

func b(v bool) *bool { return &v }

func env(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestResolve(t *testing.T) {
	cases := []struct {
		name string
		o    Options
		want Mode
	}{
		{"tty human", Options{OutputFormat: "table", Getenv: env(nil), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{Interactive: true, Color: true}},
		{"piped stdout", Options{OutputFormat: "table", Getenv: env(nil), StdoutTTY: b(false), StdinTTY: b(true)}, Mode{}},
		{"json on tty", Options{OutputFormat: "json", Getenv: env(nil), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{}},
		{"CI", Options{OutputFormat: "table", Getenv: env(map[string]string{"CI": "1"}), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{}},
		{"NO_COLOR", Options{OutputFormat: "table", Getenv: env(map[string]string{"NO_COLOR": "1"}), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{Interactive: true}},
		{"--yes", Options{OutputFormat: "table", YesFlag: true, Getenv: env(nil), StdoutTTY: b(false), StdinTTY: b(false)}, Mode{AssumeYes: true}},
		{"DAYTONA_YES", Options{OutputFormat: "table", Getenv: env(map[string]string{"DAYTONA_YES": "true"}), StdoutTTY: b(false), StdinTTY: b(false)}, Mode{AssumeYes: true}},
		{"confirm=false", Options{OutputFormat: "table", ConfirmOff: true, Getenv: env(nil), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{Interactive: true, Color: true, AssumeYes: true}},
		{"confirm=false off a terminal", Options{OutputFormat: "table", ConfirmOff: true, Getenv: env(nil), StdoutTTY: b(false), StdinTTY: b(false)}, Mode{}},
		{"confirm=false with json", Options{OutputFormat: "json", ConfirmOff: true, Getenv: env(nil), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{}},
		{"confirm=false in CI", Options{OutputFormat: "table", ConfirmOff: true, Getenv: env(map[string]string{"CI": "1"}), StdoutTTY: b(true), StdinTTY: b(true)}, Mode{}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Resolve(c.o); got != c.want {
				t.Fatalf("got %+v want %+v", got, c.want)
			}
		})
	}
}
