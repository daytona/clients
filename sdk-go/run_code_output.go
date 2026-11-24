// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"io"
	"slices"
	"strings"
)

type runCodeOutput struct {
	options RunCodeOptions
	result  RunCodeResult
	stdout  strings.Builder
	stderr  strings.Builder
	seen    map[string]struct{}
}

func newRunCodeOutput(options RunCodeOptions) *runCodeOutput {
	return &runCodeOutput{
		options: options, result: RunCodeResult{Results: []InterpreterOutput{}},
		seen: make(map[string]struct{}),
	}
}

func (o *runCodeOutput) append(frame LogFrame) error {
	if _, duplicate := o.seen[frame.Cursor]; duplicate {
		return nil
	}
	o.seen[frame.Cursor] = struct{}{}
	if o.options.OnLog != nil {
		if err := o.options.OnLog(frame); err != nil {
			return &outputDeliveryError{wrapError(err, "interpreter log callback failed")}
		}
	}
	switch frame.Channel {
	case LogChannelStdout:
		o.stdout.WriteString(frame.Data)
		return writeInterpreterOutput(o.options.Stdout, frame.Data)
	case LogChannelStderr:
		o.stderr.WriteString(frame.Data)
		return writeInterpreterOutput(o.options.Stderr, frame.Data)
	case LogChannelDisplay, LogChannelExecuteResult:
		return o.appendOutput(frame)
	case LogChannelError:
		return o.appendError(frame.Data)
	case LogChannelSetup, LogChannelPTY, LogChannelUnknown:
		// Provisioning narration and future channels are exposed only by OnLog.
	}
	return nil
}

func (o *runCodeOutput) appendOutput(frame LogFrame) error {
	output, valid, err := parseInterpreterOutput(frame)
	if err != nil || !valid {
		return err
	}
	o.result.Results = append(o.result.Results, output)
	if o.options.OnOutput != nil {
		if err = o.options.OnOutput(cloneInterpreterOutput(output)); err != nil {
			return &outputDeliveryError{wrapError(err, "interpreter output callback failed")}
		}
	}
	return nil
}

func (o *runCodeOutput) appendError(payload string) error {
	exception, valid, err := parseInterpreterError(payload)
	if err != nil || !valid {
		return err
	}
	o.result.Error = &exception
	if o.options.OnError != nil {
		copyError := exception
		copyError.Traceback = slices.Clone(exception.Traceback)
		if err = o.options.OnError(copyError); err != nil {
			return &outputDeliveryError{wrapError(err, "interpreter error callback failed")}
		}
	}
	return nil
}

func (o *runCodeOutput) finish(process InterpreterProcess) *RunCodeResult {
	o.result.Process = process
	o.result.Logs = InterpreterLogs{Stdout: o.stdout.String(), Stderr: o.stderr.String()}
	return &o.result
}

func writeInterpreterOutput(writer io.Writer, data string) error {
	if writer == nil {
		return nil
	}
	written, err := io.WriteString(writer, data)
	if err != nil {
		return &outputDeliveryError{wrapError(err, "write interpreter output")}
	}
	if written != len(data) {
		return &outputDeliveryError{wrapError(io.ErrShortWrite, "write interpreter output")}
	}
	return nil
}

func cloneInterpreterOutput(output InterpreterOutput) InterpreterOutput {
	result := output
	result.Data = make(map[string]JSONValue, len(output.Data))
	for mime, value := range output.Data {
		result.Data[mime] = slices.Clone(value)
	}
	result.JSON = slices.Clone(output.JSON)
	result.Text, result.HTML = clonePointer(output.Text), clonePointer(output.HTML)
	result.PNG, result.SVG = clonePointer(output.PNG), clonePointer(output.SVG)
	return result
}
