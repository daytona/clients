// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
	"time"
)

func parseProcess(data json.RawMessage) (*ProcessInfo, error) {
	object, err := parseObject(data, "process")
	if err != nil {
		return nil, err
	}
	info := &ProcessInfo{}
	if err = processInfoStrings(object, info); err != nil {
		return nil, err
	}
	if _, err = pathSegment(info.ID); err != nil {
		return nil, err
	}
	info.Kind, info.State = processKind(info.RawKind), processState(info.RawState)
	info.Stdin, info.KeepLogs = processStdin(info.RawStdin), processRetention(info.RawKeepLogs)
	wait, err := parseProcessWait(object)
	if err != nil {
		return nil, err
	}
	info.WaitResult = *wait
	if err = processInfoFields(object, info); err != nil {
		return nil, err
	}
	return info, nil
}

func processInfoStrings(object jsonObject, info *ProcessInfo) error {
	for _, entry := range []struct {
		name     string
		target   *string
		required bool
	}{
		{"id", &info.ID, true},
		{"kind", &info.RawKind, true},
		{"state", &info.RawState, true},
		{"cwd", &info.Cwd, true},
		{"stdin", &info.RawStdin, true},
		{"keepLogs", &info.RawKeepLogs, true},
		{"name", &info.Name, false},
		{"tag", &info.Tag, false},
		{"sessionId", &info.SessionID, false},
		{"shellCommand", &info.ShellCommand, false},
		{"code", &info.Code, false},
		{"language", &info.Language, false},
		{"shell", &info.Shell, false},
		{"resolvedShell", &info.ResolvedShell, false},
		{"user", &info.User, false},
		{"firstAvailableCursor", &info.FirstAvailableCursor, false},
		{"lastCursor", &info.LastCursor, false},
	} {
		value, err := field[string](object, entry.name, entry.required)
		if err != nil {
			return err
		}
		*entry.target = value
	}
	return nil
}

func processInfoFields(object jsonObject, info *ProcessInfo) error {
	var truncatedErr, systemErr, loginErr, pidErr error
	info.TruncatedHead, truncatedErr = field[bool](object, "truncatedHead", true)
	info.System, systemErr = field[bool](object, "system", false)
	info.Login, loginErr = field[*bool](object, "login", false)
	info.PID, pidErr = processInt32(object, "pid")
	err := errors.Join(truncatedErr, systemErr, loginErr, pidErr)
	if err != nil {
		return err
	}
	if present(object["argv"]) {
		info.Argv, err = processStringList(object, "argv")
		if err != nil {
			return err
		}
	}
	if present(object["terminal"]) {
		info.Terminal, err = parseProcessTerminal(object["terminal"])
		if err != nil {
			return err
		}
	}
	if present(object["timeoutMs"]) {
		value, parseErr := integerField(object, "timeoutMs", true)
		if parseErr != nil {
			return parseErr
		}
		// Duration cannot represent the entire JSON safe-integer range in milliseconds.
		if value < 0 || value > math.MaxInt64/int64(time.Millisecond) {
			return badRequest("response field timeoutMs cannot be represented as a non-negative duration")
		}
		timeout := time.Duration(value) * time.Millisecond
		info.ExecutionTimeout = &timeout
	}
	if !present(object["createdAt"]) {
		return badRequest("response field createdAt is required")
	}
	for _, entry := range []struct {
		name   string
		target *time.Time
	}{
		{"createdAt", &info.CreatedAt}, {"startedAt", &info.StartedAt}, {"exitedAt", &info.ExitedAt},
	} {
		value, parseErr := timestampField(object, entry.name)
		if parseErr != nil {
			return parseErr
		}
		*entry.target = value
	}
	return nil
}

func parseProcessWait(object jsonObject) (*WaitResult, error) {
	reason, reasonErr := field[string](object, "reason", false)
	signal, signalErr := field[string](object, "signal", false)
	exit, exitErr := processInt32(object, "exitCode")
	if err := errors.Join(reasonErr, signalErr, exitErr); err != nil {
		return nil, err
	}
	bare := strings.TrimPrefix(signal, "SIG")
	return &WaitResult{
		Reason: processReason(reason), RawReason: reason, ExitCode: exit,
		Signal: processSignal(bare), RawSignal: bare,
	}, nil
}

func processInt32(object jsonObject, name string) (*int, error) {
	if !present(object[name]) {
		return nil, nil //nolint:nilnil // Nil represents an absent optional integer.
	}
	value, err := integerField(object, name, true)
	if err != nil {
		return nil, err
	}
	if value < math.MinInt32 || value > math.MaxInt32 {
		return nil, badRequest("response field " + name + " must be int32")
	}
	result := int(value)
	return &result, nil
}

func parseProcessTerminal(data json.RawMessage) (*TerminalSize, error) {
	object, err := parseObject(data, "terminal")
	if err != nil {
		return nil, err
	}
	cols, colsErr := integerField(object, "cols", true)
	rows, rowsErr := integerField(object, "rows", true)
	if err = errors.Join(colsErr, rowsErr); err != nil {
		return nil, err
	}
	if cols < 1 || cols > 65535 || rows < 1 || rows > 65535 {
		return nil, badRequest("response terminal dimensions must be between 1 and 65535")
	}
	return &TerminalSize{Cols: int(cols), Rows: int(rows)}, nil
}

func processStringList(object jsonObject, name string) ([]string, error) {
	items, err := field[[]json.RawMessage](object, name, true)
	if err != nil {
		return nil, err
	}
	result := make([]string, len(items))
	for i, raw := range items {
		if !present(raw) {
			return nil, badRequest("response field " + name + " must contain strings")
		}
		if err = json.Unmarshal(raw, &result[i]); err != nil {
			return nil, badRequest("response field " + name + " must contain strings")
		}
	}
	return result, nil
}
