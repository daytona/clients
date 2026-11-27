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
	setup   strings.Builder
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
	case LogChannelSetup:
		// Provisioning narration: exposed by OnLog, and the diagnostic of a failed run.
		o.setup.WriteString(frame.Data)
	case LogChannelPTY, LogChannelUnknown:
		// Future channels are exposed only by OnLog.
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

// setupFailure is the error of a run that ended failed: the sandbox could not
// provision, start or reach the kernel, so the code did not run.
func (o *runCodeOutput) setupFailure(processID string) error {
	diagnostic := strings.TrimSpace(o.setup.String())
	if diagnostic == "" {
		diagnostic = "the sandbox reported no setup diagnostic"
	}
	return newSDKError(CodeKernelUnavailable, 503,
		"the interpreter did not run the code (process "+processID+"): "+diagnostic)
}

// complete returns the result of a finished run, with the setup failure of a
// run that ended failed.
func (o *runCodeOutput) complete(processID string, wait WaitResult) (*RunCodeResult, error) {
	result := o.finish(InterpreterProcess{ProcessID: processID, WaitResult: wait})
	if wait.Reason == ProcessReasonFailed {
		return result, o.setupFailure(processID)
	}
	return result, nil
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
