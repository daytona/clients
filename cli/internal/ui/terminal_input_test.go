// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package ui

import (
	"io"
	"os"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/muesli/cancelreader"
)

func TestEscapeUsesExistingInputReader(t *testing.T) {
	// Given queued input owned by the prompt's existing reader, not stdin.
	in, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	defer writer.Close()
	reader, err := cancelreader.NewReader(strings.NewReader("\x1b[B\x1b[200~y\x1b[201~n"))
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	p := terminalPrompt{ctx: t.Context(), in: in, reader: reader}
	// When parsing successive escapes, including bracketed paste.
	for _, want := range []string{keyDown, keyPaste, "n"} {
		got, err := p.readKey()
		// Then all queued input is consumed through the original reader.
		if err != nil || got != want {
			t.Fatalf("got %q, %v; want %q", got, err, want)
		}
	}
}

func TestEscapeResetsTimeoutForEachByte(t *testing.T) {
	for _, tc := range []struct{ name, input, want string }{
		{"arrow", "\x1b[B", keyDown},
		{"paste", "\x1b[200~y\x1b[201~", keyPaste},
	} {
		t.Run(tc.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				// Given control input fragmented across multiple escape timeouts.
				input, output := io.Pipe()
				defer input.Close()
				reader, err := cancelreader.NewReader(input)
				if err != nil {
					t.Fatal(err)
				}
				defer reader.Close()
				p := terminalPrompt{ctx: t.Context(), reader: reader}
				go func() {
					defer output.Close()
					for _, b := range []byte(tc.input) {
						time.Sleep(40 * time.Millisecond)
						if _, err := output.Write([]byte{b}); err != nil {
							t.Error(err)
							return
						}
					}
				}()
				// When reading the fragmented control input.
				got, err := p.readKey()
				// Then its action survives the total transmission time.
				if err != nil || got != tc.want {
					t.Fatalf("got %q, %v; want %q", got, err, tc.want)
				}
			})
		})
	}
}

func TestEscapeTimeoutPreservesConsumedPrefixAndLateInput(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given a sequence whose suffix arrives after the inter-byte timeout.
		input, output := io.Pipe()
		defer input.Close()
		reader, err := cancelreader.NewReader(input)
		if err != nil {
			t.Fatal(err)
		}
		defer reader.Close()
		p := terminalPrompt{ctx: t.Context(), reader: reader}
		go func() {
			defer output.Close()
			if _, err := io.WriteString(output, "\x1b[2"); err != nil {
				t.Error(err)
				return
			}
			time.Sleep(100 * time.Millisecond)
			if _, err := io.WriteString(output, "x"); err != nil {
				t.Error(err)
			}
		}()
		// When the escape times out and the caller continues reading input.
		for _, want := range []string{keyEscape, "[", "2", "x"} {
			got, err := p.readKey()
			// Then both the consumed prefix and the outstanding read survive.
			if err != nil || got != want {
				t.Fatalf("got %q, %v; want %q", got, err, want)
			}
		}
	})
}
