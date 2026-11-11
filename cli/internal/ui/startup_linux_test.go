// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui_test

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/ui"
	"golang.org/x/sys/unix"
	"golang.org/x/term"
)

func TestCLIStartupWithoutTerminalReplies(t *testing.T) {
	// Given a real CLI and a terminal that never answers terminal queries.
	binary := filepath.Join(t.TempDir(), "daytona")
	build := exec.CommandContext(t.Context(), "go", "build", "-o", binary, "../..")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build CLI: %v\n%s", err, out)
	}
	for _, args := range [][]string{
		{"--version"}, {"--help"}, {"config", "get", "confirm", "-o", "json"},
		{"config", "get", "confirm", "--quiet"},
	} {
		for _, color := range []string{"", "1"} {
			t.Run(strings.Join(args, "_")+"/NO_COLOR="+color, func(t *testing.T) {
				ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
				defer cancel()
				cmd := exec.CommandContext(ctx, binary, args...)
				cmd.Env = []string{"TERM=xterm-256color", "DAYTONA_CONFIG_DIR=" + t.TempDir()}
				if color != "" {
					cmd.Env = append(cmd.Env, "NO_COLOR="+color)
				}
				// When starting on the PTY without sending any replies.
				out, err := runTerminal(t, cmd, terminalInput{})
				// Then neither background/cursor probes nor their timeout occur.
				if strings.Contains(out, "\x1b]11;") || strings.Contains(out, "\x1b[6n") {
					t.Errorf("terminal queries in output: %q", out)
				}
				if err != nil {
					t.Fatalf("CLI did not complete: %v (%v), output %q", err, ctx.Err(), out)
				}
			})
		}
	}
}

func TestPromptKeyboardBehavior(t *testing.T) {
	for _, tc := range []struct {
		name, prompt, input, result string
		early                       bool
	}{
		{"confirm default", "confirm", "\r", "false", false},
		{"confirm yes", "confirm", "y", "true", false},
		{"confirm early yes", "confirm", "y", "true", true},
		{"confirm uppercase yes", "confirm", "Y", "true", false},
		{"confirm no", "confirm", "n", "false", false},
		{"confirm arrow", "confirm", "\x1b[D\r", "true", false},
		{"confirm toggle", "confirm", "l\r", "true", false},
		{"confirm abort", "confirm", "\x03", "false", false},
		{"confirm pasted y ignored", "confirm", "\x1b[200~y\x1b[201~n", "false", false},
		{"confirm pasted yes ignored", "confirm", "\x1b[200~yes\x1b[201~n", "false", false},
		{"confirm pasted no ignored", "confirm", "\x1b[200~n\x1b[201~y", "true", false},
		{"confirm pasted controls ignored", "confirm", "\x1b[200~\x1b[D\r\x03\x1b[201~n", "false", false},
		{"select default", "select", "\r", "first", false},
		{"select down", "select", "\x1b[B\r", "second", false},
		{"select wrap", "select", "\x1b[A\r", "third", false},
		{"select filter", "select", "/second\r", "second", false},
		{"select filtered arrow", "select", "/i\x1b[B\r", "third", false},
		{"select escape then submit", "select", "/second\x1b\r", "second", false},
		{"select abort", "select", "\x03", "aborted", false},
		{"select paste ignored", "select", "\x1b[200~jj\r\x1b[201~\x1b[B\r", "second", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given a fresh process with an interactive terminal.
			binary, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(t.Context(), 3*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, binary, "-test.run=^TestPromptProcess$")
			cmd.Env = []string{"TERM=xterm-256color", "CLI_TEST_PROMPT=" + tc.prompt}
			// When pressing keys after the prompt has opened its raw input.
			input := terminalInput{prompt: tc.input}
			if tc.early {
				input = terminalInput{early: tc.input}
			}
			out, err := runTerminal(t, cmd, input)
			// Then the real prompt returns the selected value without probing.
			if err != nil {
				t.Fatalf("prompt failed: %v: %q", err, out)
			}
			if !strings.Contains(out, "PROMPT_RESULT="+tc.result+"\r\n") {
				t.Fatalf("unexpected result: %q", out)
			}
			if strings.Contains(out, "\x1b]11;") || strings.Contains(out, "\x1b[6n") {
				t.Fatalf("prompt issued a terminal query: %q", out)
			}
		})
	}
}

func TestPromptProcess(t *testing.T) {
	if os.Getenv("CLI_TEST_PROMPT") == "" {
		return
	}
	before, err := term.GetState(int(os.Stdin.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	switch os.Getenv("CLI_TEST_PROMPT") {
	case "confirm":
		value, err := ui.Confirm("Confirm")
		if err != nil {
			t.Fatal(err)
		}
		fmt.Printf("PROMPT_RESULT=%t\n", value)
	case "select":
		value, err := ui.Select("Select", []ui.Option{
			{Label: "first", Value: "first"}, {Label: "second", Value: "second"}, {Label: "third", Value: "third"},
		})
		if errors.Is(err, ui.ErrPromptAborted) {
			value = "aborted"
		} else if err != nil {
			t.Fatal(err)
		}
		fmt.Printf("PROMPT_RESULT=%s\n", value)
	}
	after, err := term.GetState(int(os.Stdin.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatal("prompt did not restore terminal state")
	}
}

type terminalInput struct {
	early  string
	prompt string
	signal os.Signal
}

func runTerminal(t *testing.T, cmd *exec.Cmd, input terminalInput) (string, error) {
	t.Helper()
	master, err := os.OpenFile("/dev/ptmx", os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer master.Close()
	if err := unix.IoctlSetPointerInt(int(master.Fd()), unix.TIOCSPTLCK, 0); err != nil {
		t.Fatal(err)
	}
	number, err := unix.IoctlGetInt(int(master.Fd()), unix.TIOCGPTN)
	if err != nil {
		t.Fatal(err)
	}
	slave, err := os.OpenFile(fmt.Sprintf("/dev/pts/%d", number), os.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer slave.Close()
	cmd.Stdin, cmd.Stdout, cmd.Stderr = slave, slave, slave
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true, Setctty: true, Ctty: 0}
	if _, err := io.WriteString(master, input.early); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		return "", err
	}
	if err := slave.Close(); err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	var readErr error
	buffer := make([]byte, 4096)
	for {
		n, err := master.Read(buffer)
		out.Write(buffer[:n])
		if input.prompt != "" && strings.Contains(out.String(), "\x1b[2K") {
			if _, err := io.WriteString(master, input.prompt); err != nil {
				t.Fatal(err)
			}
			input.prompt = ""
		}
		if input.signal != nil && strings.Contains(out.String(), "\x1b[2K") {
			if err := cmd.Process.Signal(input.signal); err != nil {
				t.Fatal(err)
			}
			input.signal = nil
		}
		if err != nil {
			readErr = err
			break
		}
	}
	err = cmd.Wait()
	// Linux PTYs signal the slave's closure with EIO rather than EOF.
	if readErr != nil && !errors.Is(readErr, syscall.EIO) {
		return out.String(), readErr
	}
	return out.String(), err
}
