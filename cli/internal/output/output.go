// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package output renders command results: a styled table for humans, JSON or
// YAML for scripts and agents. Payload goes to stdout, diagnostics to stderr.
// Lists carry the partial-result envelope.
package output

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/lipgloss/table"
	"gopkg.in/yaml.v3"
)

// Format is the value of --output.
type Format string

const (
	FormatTable Format = "table"
	FormatJSON  Format = "json"
	FormatYAML  Format = "yaml"
)

// ParseFormat validates --output.
func ParseFormat(s string) (Format, error) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "", "table":
		return FormatTable, nil
	case "json":
		return FormatJSON, nil
	case "yaml", "yml":
		return FormatYAML, nil
	}
	return "", fmt.Errorf("unknown --output %q (want table|json|yaml)", s)
}

// Printer renders to an io.Writer in one Format.
type Printer struct {
	W      io.Writer
	Format Format
	Color  bool
}

// RegionError is one failed region in a fan-out list: the HTTP status and
// the server's error code, source and trace id when the region's API
// answered.
type RegionError struct {
	Region  string `json:"region"`
	Status  int    `json:"status,omitempty"`
	Code    string `json:"code,omitempty"`
	Source  string `json:"source,omitempty"`
	TraceID string `json:"trace_id,omitempty"`
	Message string `json:"message"`
	// Err is the failure itself (not printed).
	Err error `json:"-"`
}

// List is the envelope every list command emits. ListOut prints Items as an
// array even when it is nil.
type List[T any] struct {
	Items      []T           `json:"items"`
	NextCursor string        `json:"next_cursor,omitempty"`
	Incomplete bool          `json:"incomplete"`
	Errors     []RegionError `json:"errors,omitempty"`
}

// Table is what a command supplies for human rendering.
type Table struct {
	Headers []string
	Rows    [][]string
}

// Object prints one structured value. In table mode it renders key/value
// rows from `kv`; JSON/YAML print `v` verbatim.
func (p Printer) Object(v any, kv [][2]string) error {
	switch p.Format {
	case FormatJSON:
		return p.json(v)
	case FormatYAML:
		return p.yaml(v)
	}
	tbl := Table{Headers: nil}
	for _, e := range kv {
		tbl.Rows = append(tbl.Rows, []string{e[0], e[1]})
	}
	return p.table(tbl, true)
}

// TableOf prints one structured value `v` verbatim in JSON/YAML and the
// supplied table for humans — for responses that are not a list envelope
// but still read as rows (the per-region CPU catalog).
func (p Printer) TableOf(v any, t Table) error {
	switch p.Format {
	case FormatJSON:
		return p.json(v)
	case FormatYAML:
		return p.yaml(v)
	}
	if len(t.Rows) == 0 {
		_, err := fmt.Fprintln(p.W, "No results.")
		return err
	}
	return p.table(t, false)
}

// ListOut prints a list envelope; `tbl` builds the human table from items.
func ListOut[T any](p Printer, l List[T], tbl func([]T) Table) error {
	if l.Items == nil {
		l.Items = []T{} // `items` is always an array, never null
	}
	switch p.Format {
	case FormatJSON:
		return p.json(l)
	case FormatYAML:
		return p.yaml(l)
	}
	if len(l.Items) == 0 && !l.Incomplete {
		_, err := fmt.Fprintln(p.W, "No results.")
		return err
	}
	if err := p.table(tbl(l.Items), false); err != nil {
		return err
	}
	if l.NextCursor != "" {
		fmt.Fprintf(p.W, "\nMore results: --cursor %s  (or --all)\n", Sanitize(l.NextCursor))
	}
	return nil
}

// Raw prints a pre-formatted string (human mode) or a {"value": …} object.
func (p Printer) Raw(s string, v any) error {
	switch p.Format {
	case FormatJSON:
		return p.json(v)
	case FormatYAML:
		return p.yaml(v)
	}
	_, err := fmt.Fprintln(p.W, Sanitize(s))
	return err
}

func (p Printer) json(v any) error {
	enc := json.NewEncoder(p.W)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func (p Printer) yaml(v any) error {
	// Round-trip through JSON so struct tags decide the field names.
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var generic any
	if err := dec.Decode(&generic); err != nil {
		return err
	}
	generic = normalizeYAMLNumbers(generic)
	out, err := yaml.Marshal(generic)
	if err != nil {
		return err
	}
	_, err = p.W.Write(out)
	return err
}

func normalizeYAMLNumbers(v any) any {
	switch node := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(node))
		for k, child := range node {
			out[k] = normalizeYAMLNumbers(child)
		}
		return out
	case []any:
		out := make([]any, 0, len(node))
		for _, child := range node {
			out = append(out, normalizeYAMLNumbers(child))
		}
		return out
	case json.Number:
		if i, err := node.Int64(); err == nil {
			return i
		}
		if f, err := node.Float64(); err == nil {
			return f
		}
		return node.String()
	default:
		return v
	}
}

// Sanitize strips terminal control sequences from a server-controlled string
// before it reaches a terminal: names, labels and messages come from other
// tenants' input and could otherwise carry ESC/CSI/OSC sequences that
// re-title the window, move the cursor, or hide text. Printable text (any
// script) passes through; C0/C1 controls and ESC-initiated sequences are
// dropped, except LF which is kept (logs are multi-line; a table cell never
// contains one). JSON/YAML output is untouched — it is data, not a terminal.
func Sanitize(s string) string {
	// Fast path on BYTES (not decoded runes): a raw 0x80–0x9f byte is a C1
	// control to a terminal even though it is invalid UTF-8, and the rune
	// decoder would report it as RuneError and let it through.
	clean := true
	for i := 0; i < len(s); i++ {
		if b := s[i]; b < 0x20 || (b >= 0x7f && b <= 0x9f) || b == 0xff || b == 0xfe {
			clean = false
			break
		}
	}
	if clean {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	skipCSI, skipOSC := false, false
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		if r == utf8.RuneError && size == 1 {
			// A raw non-UTF-8 byte: treat the C1 range (0x80–0x9f) as the
			// control it is (e.g. ST 0x9c) and drop anything else invalid.
			if b := s[i]; b >= 0x80 && b <= 0x9f {
				r = rune(b)
			} else {
				i++
				continue
			}
		}
		i += size
		switch {
		case skipOSC:
			// OSC ends with BEL, the C1 ST (0x9c) or the 7-bit ST (ESC \).
			if r == 0x07 || r == 0x9c {
				skipOSC = false
			} else if r == 0x1b && i < len(s) && s[i] == '\\' {
				skipOSC = false
				i++
			}
		case skipCSI:
			// CSI parameters/intermediates are 0x20–0x3f; the final byte 0x40–0x7e ends it.
			if r >= 0x40 && r <= 0x7e {
				skipCSI = false
			}
		case r == 0x1b:
			if i < len(s) {
				switch s[i] {
				case '[':
					skipCSI = true
					i++
				case ']':
					skipOSC = true
					i++
				default:
					i++ // two-byte escape (ESC x)
				}
			}
		case r == 0x9b: // C1 CSI — same grammar as ESC [
			skipCSI = true
		case r == 0x9d: // C1 OSC — same grammar as ESC ]
			skipOSC = true
		case r == '\t':
			b.WriteByte(' ')
		case r == '\n':
			b.WriteByte('\n') // multi-line payloads (logs) keep their line breaks
		case r < 0x20 || (r >= 0x7f && r <= 0x9f):
			// drop other C0/C1 controls (CR, BS, DEL, C1 …)
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// SanitizeLine is Sanitize for a value that must stay on ONE line (a table
// cell, a URL or code shown in a prompt): LF becomes a space, so untrusted
// text cannot fake additional instruction lines.
func SanitizeLine(s string) string {
	return strings.ReplaceAll(Sanitize(s), "\n", " ")
}

type StreamSanitizer struct {
	W          io.Writer
	skipCSI    bool
	skipOSC    bool
	escPending bool
	// utf8Tail holds an incomplete trailing UTF-8 sequence (at most 3
	// bytes) an HTTP read split across writes; it is prepended to the next
	// write so the rune is decoded whole instead of dropped.
	utf8Tail []byte
}

func NewStreamSanitizer(w io.Writer) *StreamSanitizer {
	return &StreamSanitizer{W: w}
}

func (s *StreamSanitizer) Write(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	data := p
	if len(s.utf8Tail) > 0 {
		data = append(append([]byte{}, s.utf8Tail...), p...)
		s.utf8Tail = nil
	}
	// Hold back an incomplete final rune for the next write.
	data, s.utf8Tail = splitIncompleteRune(data)
	chunk := s.sanitizeChunk(string(data))
	if chunk == "" {
		return len(p), nil
	}
	_, err := io.WriteString(s.W, chunk)
	return len(p), err
}

// Flush writes any held-back partial rune as-is (the stream ended inside a
// character): the sanitizer replaces it like any other invalid byte.
func (s *StreamSanitizer) Flush() error {
	if len(s.utf8Tail) == 0 {
		return nil
	}
	tail := s.utf8Tail
	s.utf8Tail = nil
	chunk := s.sanitizeChunk(string(tail))
	if chunk == "" {
		return nil
	}
	_, err := io.WriteString(s.W, chunk)
	return err
}

func (s *StreamSanitizer) sanitizeChunk(input string) string {
	var b strings.Builder
	b.Grow(len(input))
	for i := 0; i < len(input); {
		if s.escPending {
			s.escPending = false
			if s.skipOSC {
				if input[i] == '\\' {
					s.skipOSC = false
					i++
				}
				continue
			}
			switch input[i] {
			case '[':
				s.skipCSI = true
				i++
				continue
			case ']':
				s.skipOSC = true
				i++
				continue
			default:
				i++
				continue
			}
		}

		r, size := utf8.DecodeRuneInString(input[i:])
		if r == utf8.RuneError && size == 1 {
			if raw := input[i]; raw >= 0x80 && raw <= 0x9f {
				r = rune(raw)
			} else {
				i++
				continue
			}
		}
		i += size
		switch {
		case s.skipOSC:
			if r == 0x07 || r == 0x9c {
				s.skipOSC = false
			} else if r == 0x1b {
				if i >= len(input) {
					s.escPending = true
				} else if input[i] == '\\' {
					s.skipOSC = false
					i++
				}
			}
		case s.skipCSI:
			if r >= 0x40 && r <= 0x7e {
				s.skipCSI = false
			}
		case r == 0x1b:
			if i >= len(input) {
				s.escPending = true
				continue
			}
			switch input[i] {
			case '[':
				s.skipCSI = true
				i++
			case ']':
				s.skipOSC = true
				i++
			default:
				i++
			}
		case r == 0x9b:
			s.skipCSI = true
		case r == 0x9d:
			s.skipOSC = true
		case r == '\n' || r == '\t':
			b.WriteRune(r)
		case r < 0x20 || (r >= 0x7f && r <= 0x9f):
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

func sanitizeCell(s string) string { return SanitizeLine(s) }

func sanitizeTable(t Table) Table {
	out := Table{Headers: make([]string, len(t.Headers)), Rows: make([][]string, len(t.Rows))}
	for i, h := range t.Headers {
		out.Headers[i] = sanitizeCell(h)
	}
	for i, row := range t.Rows {
		out.Rows[i] = make([]string, len(row))
		for j, c := range row {
			out.Rows[i][j] = sanitizeCell(c)
		}
	}
	return out
}

func (p Printer) table(t Table, keyValue bool) error {
	t = sanitizeTable(t)
	if !p.Color {
		return p.plainTable(t)
	}
	tb := table.New().Border(lipgloss.HiddenBorder()).BorderRow(false).
		StyleFunc(func(row, col int) lipgloss.Style {
			s := lipgloss.NewStyle().PaddingRight(2)
			if row == table.HeaderRow || (keyValue && col == 0) {
				return s.Bold(true).Foreground(lipgloss.Color("39"))
			}
			return s
		})
	if len(t.Headers) > 0 {
		tb = tb.Headers(t.Headers...)
	}
	tb = tb.Rows(t.Rows...)
	_, err := fmt.Fprintln(p.W, tb.Render())
	return err
}

func (p Printer) plainTable(t Table) error {
	widths := make([]int, 0)
	all := t.Rows
	if len(t.Headers) > 0 {
		all = append([][]string{t.Headers}, t.Rows...)
	}
	for _, r := range all {
		for i, c := range r {
			for len(widths) <= i {
				widths = append(widths, 0)
			}
			if len(c) > widths[i] {
				widths[i] = len(c)
			}
		}
	}
	var sb strings.Builder
	for _, r := range all {
		for i, c := range r {
			if i == len(r)-1 {
				sb.WriteString(c)
			} else {
				sb.WriteString(c)
				sb.WriteString(strings.Repeat(" ", widths[i]-len(c)+2))
			}
		}
		sb.WriteByte('\n')
	}
	_, err := io.WriteString(p.W, sb.String())
	return err
}

// NDJSONDataWriter turns a byte stream into NDJSON: every write becomes one
// {"data": "<text>"} line. A UTF-8 sequence split across writes is held back
// until it is complete, so no line carries half a character.
type NDJSONDataWriter struct {
	enc  *json.Encoder
	tail []byte
}

// NewNDJSONDataWriter returns an NDJSONDataWriter writing to w.
func NewNDJSONDataWriter(w io.Writer) *NDJSONDataWriter {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return &NDJSONDataWriter{enc: enc}
}

func (n *NDJSONDataWriter) Write(p []byte) (int, error) {
	data := append(n.tail, p...)
	n.tail = nil
	data, n.tail = splitIncompleteRune(data)
	if len(data) == 0 {
		return len(p), nil
	}
	if err := n.enc.Encode(map[string]string{"data": string(data)}); err != nil {
		return 0, err
	}
	return len(p), nil
}

// Flush writes a held-back partial character (the stream ended inside it).
func (n *NDJSONDataWriter) Flush() error {
	if len(n.tail) == 0 {
		return nil
	}
	tail := n.tail
	n.tail = nil
	return n.enc.Encode(map[string]string{"data": string(tail)})
}

// splitIncompleteRune splits data before an incomplete final UTF-8 sequence
// (at most 3 bytes) that a read split across writes; rest is a copy, nil when
// data ends on a whole rune.
func splitIncompleteRune(data []byte) (complete, rest []byte) {
	for back := 1; back <= 3 && back <= len(data); back++ {
		tail := data[len(data)-back:]
		if !utf8.RuneStart(tail[0]) {
			continue
		}
		if utf8.FullRune(tail) {
			return data, nil
		}
		cut := len(data) - back
		return data[:cut], append([]byte{}, data[cut:]...)
	}
	return data, nil
}
