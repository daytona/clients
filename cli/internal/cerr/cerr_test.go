// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cerr

import (
	"errors"
	"fmt"
	"testing"
)

func TestExitCode(t *testing.T) {
	if ExitCode(nil) != 0 {
		t.Fatal("nil -> 0")
	}
	if ExitCode(errors.New("x")) != ExitFailure {
		t.Fatal("plain error -> 1")
	}
	e := fmt.Errorf("wrapped: %w", New(ExitForbidden, "no"))
	if ExitCode(e) != ExitForbidden {
		t.Fatal("wrapped cerr -> its code")
	}
}

func TestCodeForStatus(t *testing.T) {
	for status, want := range map[int]int{401: 3, 403: 4, 404: 5, 409: 6, 501: 6, 400: 2, 500: 1, 504: 8} {
		if got := CodeForStatus(status); got != want {
			t.Errorf("%d: got %d want %d", status, got, want)
		}
	}
}

func TestRefusals(t *testing.T) {
	e := RefuseWithoutYes("delete", "sandbox abc")
	if e.Code != ExitUsage || e.Hint == "" {
		t.Fatalf("%+v", e)
	}
	m := MissingArg("project", "--project")
	if m.Code != ExitUsage {
		t.Fatal()
	}
}
