// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/spf13/pflag"
)

// maxWireSeconds is the largest duration the API stores (an int32 of seconds).
const maxWireSeconds = math.MaxInt32

// minIdleSeconds is the shortest idle expiry the API honours; it raises any
// shorter value to this one.
const minIdleSeconds = 60

// secondsFlag is a duration flag sent to the API as whole seconds. It takes a
// Go duration (`90s`, `10m`, `1h30m`) and, where the API has a sentinel, a
// keyword such as `max` or `off`. A value that is not a whole number of
// seconds, out of range, or zero where zero is not allowed fails flag
// parsing (exit 2).
type secondsFlag struct {
	seconds   int64
	set       bool
	allowZero bool
	// min is the smallest accepted value in seconds (0 = none).
	min int64
	// keywords maps a case-insensitive keyword to the wire value it sends.
	keywords map[string]int64
	def      string
}

func (f *secondsFlag) String() string { return f.def }

func (f *secondsFlag) Type() string { return "duration" }

func (f *secondsFlag) Set(raw string) error {
	value := strings.TrimSpace(raw)
	if wire, ok := f.keywords[strings.ToLower(value)]; ok {
		f.seconds, f.set = wire, true
		return nil
	}
	d, err := time.ParseDuration(value)
	if err != nil {
		hint := "a duration such as 30s, 10m or 1h"
		if strings.IndexFunc(value, func(r rune) bool { return r < '0' || r > '9' }) < 0 && value != "" {
			hint = fmt.Sprintf("a duration with a unit, such as %ss", value)
		}
		if kw := f.keywordList(); kw != "" {
			hint += " (or " + kw + ")"
		}
		return fmt.Errorf("want %s", hint)
	}
	switch {
	case d < 0:
		return fmt.Errorf("must not be negative")
	case d == 0 && !f.allowZero:
		return fmt.Errorf("must be greater than zero")
	case d%time.Second != 0:
		return fmt.Errorf("must be a whole number of seconds")
	case d/time.Second > maxWireSeconds:
		return fmt.Errorf("must not exceed %d seconds", maxWireSeconds)
	case f.min > 0 && int64(d/time.Second) < f.min:
		return fmt.Errorf("must be at least %ds", f.min)
	}
	f.seconds, f.set = int64(d/time.Second), true
	return nil
}

func (f *secondsFlag) keywordList() string {
	if len(f.keywords) == 0 {
		return ""
	}
	names := make([]string, 0, len(f.keywords))
	for k := range f.keywords {
		names = append(names, k)
	}
	sort.Strings(names)
	return strings.Join(names, ", ")
}

// addSecondsFlag registers a secondsFlag. def is the help's displayed
// default ("" shows none); it is never sent unless the flag is given.
func addSecondsFlag(fs *pflag.FlagSet, target *secondsFlag, name, def, usage string) {
	target.def = def
	fs.Var(target, name, usage)
	flagPlaceholder(fs, name, "DURATION")
}
