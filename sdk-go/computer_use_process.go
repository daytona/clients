// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
	"time"
	"unicode"
)

// ComputerUseAddons controls individual desktop addon processes.
// It is distinct from the sandbox's general-purpose process engine.
type ComputerUseAddons struct{ s *Sandbox }

// ComputerUseProcessStatus describes an addon-managed process.
type ComputerUseProcessStatus struct {
	// Running reports whether the process is running.
	Running bool
	// Priority is its startup priority.
	Priority int
	// AutoRestart reports whether the addon restarts it automatically.
	AutoRestart bool
	// PID is absent when no process ID was reported.
	PID *int
	// Failed reports whether the process failed.
	Failed bool
	// RestartAttempts counts attempted restarts.
	RestartAttempts int
	// RestartBackoff is the current restart delay.
	RestartBackoff time.Duration
	// Status is the server's open status string.
	Status string
	// ExtraFields preserves unknown fields, with leading initialisms lowercased.
	ExtraFields map[string]JSONValue
}

// ComputerUseLifecycleResult reports addon startup or shutdown.
type ComputerUseLifecycleResult struct {
	// Message is the server explanation.
	Message string
	// Status is keyed by addon process name.
	Status map[string]ComputerUseProcessStatus
}

// ComputerUseProcessRunningStatus reports whether a named addon process is running.
type ComputerUseProcessRunningStatus struct {
	// ProcessName identifies the process.
	ProcessName string
	// Running reports whether it is running.
	Running bool
}

// ComputerUseProcessRestartResult reports a named process restart.
type ComputerUseProcessRestartResult struct {
	// Message is the server explanation.
	Message string
	// ProcessName identifies the restarted process.
	ProcessName string
}

// ComputerUseProcessLogs contains a named process's log text.
type ComputerUseProcessLogs struct {
	// ProcessName identifies the process.
	ProcessName string
	// Logs contains its log text.
	Logs string
}

// ComputerUseProcessErrors contains a named process's error text.
type ComputerUseProcessErrors struct {
	// ProcessName identifies the process.
	ProcessName string
	// Errors contains its error text.
	Errors string
}

// Status returns all addon processes (an unpaginated map).
func (s *ComputerUseAddons) Status(ctx context.Context) (map[string]ComputerUseProcessStatus, error) {
	raw, err := desktopRead(ctx, s.s, toolboxRequest{Path: "/computeruse/process-status"})
	if err != nil {
		return nil, err
	}
	return parseDesktopProcesses(raw)
}

// ProcessStatus returns one addon's running status. Invalid names return ErrBadRequest.
func (s *ComputerUseAddons) ProcessStatus(
	ctx context.Context, name string,
) (*ComputerUseProcessRunningStatus, error) {
	raw, err := s.read(ctx, name, "status")
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	result := &ComputerUseProcessRunningStatus{ProcessName: d.text("processName"), Running: d.boolean("running")}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

// Restart restarts a named addon process. Invalid names return ErrBadRequest.
func (s *ComputerUseAddons) Restart(ctx context.Context, name string) (*ComputerUseProcessRestartResult, error) {
	raw, err := s.read(ctx, name, "restart")
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	result := &ComputerUseProcessRestartResult{Message: d.text("message"), ProcessName: d.text("processName")}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

// Logs reads a named addon process's logs.
func (s *ComputerUseAddons) Logs(ctx context.Context, name string) (*ComputerUseProcessLogs, error) {
	raw, err := s.read(ctx, name, "logs")
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	result := &ComputerUseProcessLogs{ProcessName: d.text("processName"), Logs: d.text("logs")}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

// Errors reads a named addon process's error log.
func (s *ComputerUseAddons) Errors(ctx context.Context, name string) (*ComputerUseProcessErrors, error) {
	raw, err := s.read(ctx, name, "errors")
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	result := &ComputerUseProcessErrors{ProcessName: d.text("processName"), Errors: d.text("errors")}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

func (s *ComputerUseAddons) read(ctx context.Context, name, operation string) (JSONValue, error) {
	segment, err := pathSegment(name)
	if err != nil {
		return nil, err
	}
	spec := toolboxRequest{Path: "/computeruse/process/" + segment + "/" + operation}
	if operation == "restart" {
		spec.Method = http.MethodPost
	}
	return desktopRead(ctx, s.s, spec)
}

func parseDesktopProcesses(raw JSONValue) (map[string]ComputerUseProcessStatus, error) {
	object, err := parseObject(raw, "computer-use process status map")
	if err != nil {
		return nil, err
	}
	result := make(map[string]ComputerUseProcessStatus, len(object))
	for name, value := range object {
		status, parseErr := parseDesktopProcess(value)
		if parseErr != nil {
			return nil, parseErr
		}
		result[name] = status
	}
	return result, nil
}

func parseDesktopProcess(raw JSONValue) (ComputerUseProcessStatus, error) {
	d := decodeDesktop(raw)
	backoff := d.safeInteger("RestartBackoffMs")
	// Duration cannot represent the full safe-integer millisecond range.
	if backoff > int64((1<<63-1)/time.Millisecond) {
		d.check(badRequest("response field RestartBackoffMs overflows time.Duration"))
	}
	result := ComputerUseProcessStatus{
		Running: d.boolean("Running"), Priority: d.int32("Priority"), AutoRestart: d.boolean("AutoRestart"),
		Failed: d.boolean("Failed"), RestartAttempts: d.nonnegative32("RestartAttempts"),
		RestartBackoff: time.Duration(backoff) * time.Millisecond, Status: d.text("Status"),
	}
	if present(d.object["Pid"]) {
		pid := d.int32("Pid")
		result.PID = &pid
	}
	extras := d.extras("Running", "Priority", "AutoRestart", "Pid", "Failed",
		"RestartAttempts", "RestartBackoffMs", "Status")
	if len(extras) > 0 {
		result.ExtraFields = make(map[string]JSONValue, len(extras))
		for key, value := range extras {
			result.ExtraFields[desktopExtraKey(key)] = value
		}
	}
	if d.err != nil {
		return ComputerUseProcessStatus{}, d.err
	}
	return result, nil
}

func desktopExtraKey(key string) string {
	runes := []rune(key)
	end := 0
	for end < len(runes) && runes[end] >= 'A' && runes[end] <= 'Z' {
		end++
	}
	if end > 1 && end < len(runes) && unicode.IsLower(runes[end]) {
		end--
	}
	return strings.ToLower(string(runes[:end])) + string(runes[end:])
}

func parseDesktopLifecycle(raw JSONValue) (*ComputerUseLifecycleResult, error) {
	d := decodeDesktop(raw)
	message := d.text("message")
	status, err := parseDesktopProcesses(d.object["status"])
	d.check(err)
	if d.err != nil {
		return nil, d.err
	}
	return &ComputerUseLifecycleResult{Message: message, Status: status}, nil
}
