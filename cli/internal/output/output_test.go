// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package output

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

type item struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

func TestListJSONEnvelope(t *testing.T) {
	var buf bytes.Buffer
	l := List[item]{Items: []item{{"a", "x"}}, NextCursor: "c1", Incomplete: true, Errors: []RegionError{{Region: "eu", Status: 502, Message: "down"}}}
	if err := ListOut(Printer{W: &buf, Format: FormatJSON}, l, nil); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(buf.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if got["incomplete"] != true || got["next_cursor"] != "c1" || len(got["errors"].([]any)) != 1 {
		t.Fatalf("%s", buf.String())
	}
}

func TestListTablePlain(t *testing.T) {
	var buf bytes.Buffer
	l := List[item]{Items: []item{{"a", "x"}, {"bb", "yyy"}}, NextCursor: "n"}
	err := ListOut(Printer{W: &buf, Format: FormatTable}, l, func(is []item) Table {
		rows := [][]string{}
		for _, i := range is {
			rows = append(rows, []string{i.ID, i.Name})
		}
		return Table{Headers: []string{"ID", "NAME"}, Rows: rows}
	})
	if err != nil {
		t.Fatal(err)
	}
	s := buf.String()
	if !strings.HasPrefix(s, "ID  NAME\n") || !strings.Contains(s, "bb  yyy") || !strings.Contains(s, "--cursor n") {
		t.Fatalf("%q", s)
	}
}

func TestEmptyList(t *testing.T) {
	var buf bytes.Buffer
	_ = ListOut(Printer{W: &buf, Format: FormatTable}, List[item]{}, func([]item) Table { return Table{} })
	if buf.String() != "No results.\n" {
		t.Fatalf("%q", buf.String())
	}
}

func TestTableOf(t *testing.T) {
	type catalog struct {
		Regions []string `json:"regions"`
		Items   []item   `json:"items"`
	}
	v := catalog{Regions: []string{"eu"}, Items: []item{{"a", "x"}}}
	tbl := Table{Headers: []string{"ID", "NAME", "AVAILABLE"}, Rows: [][]string{{"a", "x", "exact"}}}

	var buf bytes.Buffer
	if err := (Printer{W: &buf, Format: FormatJSON}).TableOf(v, tbl); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(buf.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if _, ok := got["regions"]; !ok || len(got) != 2 {
		t.Fatalf("json should be the value verbatim, not an envelope: %s", buf.String())
	}

	buf.Reset()
	if err := (Printer{W: &buf, Format: FormatYAML}).TableOf(v, tbl); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(buf.String(), "regions:") {
		t.Fatalf("%q", buf.String())
	}

	buf.Reset()
	if err := (Printer{W: &buf, Format: FormatTable}).TableOf(v, tbl); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(buf.String(), "ID  NAME  AVAILABLE\n") || !strings.Contains(buf.String(), "a   x     exact") {
		t.Fatalf("%q", buf.String())
	}

	buf.Reset()
	if err := (Printer{W: &buf, Format: FormatTable}).TableOf(v, Table{Headers: tbl.Headers}); err != nil {
		t.Fatal(err)
	}
	if buf.String() != "No results.\n" {
		t.Fatalf("%q", buf.String())
	}
}

func TestObjectYAML(t *testing.T) {
	var buf bytes.Buffer
	if err := (Printer{W: &buf, Format: FormatYAML}).Object(item{"a", "x"}, nil); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(buf.String(), "id: a") {
		t.Fatalf("%q", buf.String())
	}
}

func TestParseFormat(t *testing.T) {
	if _, err := ParseFormat(""); err == nil {
		t.Fatal("an explicit empty format should fail")
	}
	if _, err := ParseFormat("xml"); err == nil {
		t.Fatal("xml should fail")
	}
}

func TestObjectYAML_preservesIntegerPrecision(t *testing.T) {
	var buf bytes.Buffer
	input := json.RawMessage(`{"value":9007199254740993}`)
	if err := (Printer{W: &buf, Format: FormatYAML}).Object(input, nil); err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := yaml.Unmarshal(buf.Bytes(), &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["value"] != 9007199254740993 {
		t.Fatalf("yaml lost precision: %v", decoded["value"])
	}
}

func TestSanitizeStripsTerminalControls(t *testing.T) {
	cases := map[string]string{
		"plain":                              "plain",
		"héllo wörld — ✓ 日本":                 "héllo wörld — ✓ 日本",
		"evil\x1b[31mred\x1b[0m":             "evilred",
		"title\x1b]0;pwned\x07after":         "titleafter",
		"title\x1b]0;pwned\x1b\\after":       "titleafter",
		"a\rb\nc\x08d":                       "ab\ncd",
		"title\x1b]0;pwned\x9cafter":         "titleafter",
		"line1\nline2\n":                     "line1\nline2\n",
		"tab\tsep":                           "tab sep",
		"esc-only\x1b":                       "esc-only",
		"two-byte\x1bMesc":                   "two-byteesc",
		"del\x7fchar":                        "delchar",
		"csi-long\x1b[38;5;123;48;2;1;2;3mX": "csi-longX",
		// C1 controls: as the UTF-8 rune a real server sends, and as a raw
		// 8-bit byte (invalid UTF-8) — both must be recognised.
		"title\x1b]0;pwned\u009cafter": "titleafter",
		"c1-csi\x9b31mX":               "c1-csiX",
		"c1-csi\u009b31mX":             "c1-csiX",
		"c1-osc\u009d0;pwned\u009cX":   "c1-oscX",
		"c1-osc\x9d0;pwned\x07X":       "c1-oscX",
		"bad\xffbyte":                  "badbyte",
	}
	for in, want := range cases {
		if got := Sanitize(in); got != want {
			t.Errorf("Sanitize(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestTableCellsFlattenNewlines(t *testing.T) {
	var buf bytes.Buffer
	_ = ListOut(Printer{W: &buf, Format: FormatTable}, List[item]{Items: []item{{"a", "two\nlines"}, {"b", "x"}}}, func(is []item) Table {
		t := Table{Headers: []string{"ID", "NAME"}}
		for _, i := range is {
			t.Rows = append(t.Rows, []string{i.ID, i.Name})
		}
		return t
	})
	if lines := strings.Count(strings.TrimRight(buf.String(), "\n"), "\n"); lines != 2 {
		t.Fatalf("a cell's LF must not break the row: %q", buf.String())
	}
}

func TestTableOutputIsSanitizedButJSONIsNot(t *testing.T) {
	name := "box\x1b]0;pwned\x07"
	var buf bytes.Buffer
	_ = ListOut(Printer{W: &buf, Format: FormatTable}, List[item]{Items: []item{{"a", name}}}, func(is []item) Table {
		return Table{Headers: []string{"ID", "NAME"}, Rows: [][]string{{is[0].ID, is[0].Name}}}
	})
	if strings.Contains(buf.String(), "\x1b") || !strings.Contains(buf.String(), "box") {
		t.Fatalf("table not sanitized: %q", buf.String())
	}
	buf.Reset()
	_ = ListOut(Printer{W: &buf, Format: FormatJSON}, List[item]{Items: []item{{"a", name}}}, nil)
	var got map[string]any
	_ = json.Unmarshal(buf.Bytes(), &got)
	if got["items"].([]any)[0].(map[string]any)["name"] != name {
		t.Fatal("JSON must carry the raw value — it is data, not a terminal")
	}
}

func TestStreamSanitizerCarriesSplitCSISequence(t *testing.T) {
	var buf bytes.Buffer
	stream := NewStreamSanitizer(&buf)
	_, _ = stream.Write([]byte("first\n\x1b["))
	_, _ = stream.Write([]byte("31mred\tok\n"))
	if buf.String() != "first\nred\tok\n" {
		t.Fatalf("%q", buf.String())
	}
}

// An HTTP read may split a multibyte rune across writes; the sanitizer holds
// the incomplete tail and decodes the rune whole on the next write.
func TestStreamSanitizerReassemblesSplitUTF8Rune(t *testing.T) {
	var out strings.Builder
	s := NewStreamSanitizer(&out)
	full := []byte("héllo\n") // é = 0xC3 0xA9
	_, _ = s.Write(full[:2])  // "h" + first byte of é
	_, _ = s.Write(full[2:])
	if err := s.Flush(); err != nil {
		t.Fatal(err)
	}
	if out.String() != "héllo\n" {
		t.Fatalf("got %q", out.String())
	}
	// A stream ending inside a rune flushes what it has (sanitized).
	out.Reset()
	s = NewStreamSanitizer(&out)
	_, _ = s.Write([]byte("ok\xc3"))
	_ = s.Flush()
	if !strings.HasPrefix(out.String(), "ok") {
		t.Fatalf("got %q", out.String())
	}
}

func TestStreamSanitizerDropsSplitOSCBoundaryWithoutPoisoningLaterOutput(t *testing.T) {
	var buf bytes.Buffer
	stream := NewStreamSanitizer(&buf)
	_, _ = stream.Write([]byte("before\x1b]0;title\x1b"))
	_, _ = stream.Write([]byte("[still-osc\x1b\\after"))
	if err := stream.Flush(); err != nil {
		t.Fatal(err)
	}
	if buf.String() != "beforeafter" {
		t.Fatalf("got %q", buf.String())
	}
}
