// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
	"github.com/daytona/clients/sdk-go/pkg/options"
	toolbox "github.com/daytona/clients/toolbox-api-client-go"
)

const maxProcessLogPages = 10_000

type utf8StreamDecoder struct {
	tail []byte
}

func (d *utf8StreamDecoder) Decode(chunk []byte) string {
	// Frames contain raw byte chunks, so retain an incomplete rune until the
	// next frame instead of exposing invalid UTF-8 to callbacks or results.
	data := append(d.tail, chunk...)
	complete := 0
	for complete < len(data) {
		if !utf8.FullRune(data[complete:]) {
			break
		}
		_, size := utf8.DecodeRune(data[complete:])
		complete += size
	}
	d.tail = append(d.tail[:0], data[complete:]...)
	return strings.ToValidUTF8(string(data[:complete]), "�")
}

func (d *utf8StreamDecoder) Flush() string {
	result := strings.ToValidUTF8(string(d.tail), "�")
	d.tail = nil
	return result
}

type processOutputDecoders struct {
	stdout utf8StreamDecoder
	stderr utf8StreamDecoder
}

func (d *processOutputDecoders) Decode(frame *toolbox.ProcessLogFrame) (string, string, error) {
	data := []byte(frame.GetData())
	if frame.GetEncoding() == "base64" {
		decoded, err := base64.StdEncoding.DecodeString(frame.GetData())
		if err != nil {
			return "", "", fmt.Errorf("decode process log frame: %w", err)
		}
		data = decoded
	}
	switch frame.Channel {
	case toolbox.PROCESSLOGCHANNEL_ChannelStdout, toolbox.PROCESSLOGCHANNEL_ChannelPty:
		return d.stdout.Decode(data), "", nil
	case toolbox.PROCESSLOGCHANNEL_ChannelStderr:
		return "", d.stderr.Decode(data), nil
	default:
		return "", "", nil
	}
}

func (d *processOutputDecoders) Flush() (string, string) {
	return d.stdout.Flush(), d.stderr.Flush()
}

// collectOutput replays every retained log page for the process.
//
// The daemon retains logs under a byte cap, so what it replays is a retained
// suffix rather than the full history: once the cap is reached the oldest
// frames are evicted and the page reports TruncatedHead with the surviving
// range's FirstAvailableCursor. Collected output therefore starts at the
// oldest retained frame by design. Callers that must detect or recover from
// eviction should page with [ProcessHandle.Logs] and inspect TruncatedHead and
// FirstAvailableCursor, or follow [ProcessHandle.StreamLogs] from the start of
// the process so no frame is evicted before it is read.
func (h *ProcessHandle) collectOutput(ctx context.Context) (string, string, error) {
	var stdout, stderr strings.Builder
	decoders := processOutputDecoders{}
	cursor := "start"
	drained := false
	for pageNumber := 0; pageNumber < maxProcessLogPages; pageNumber++ {
		page, err := h.Logs(ctx, cursor, 1000, "base64")
		if err != nil {
			return "", "", err
		}
		for index := range page.Frames {
			out, errOut, err := decoders.Decode(&page.Frames[index])
			if err != nil {
				return "", "", err
			}
			stdout.WriteString(out)
			stderr.WriteString(errOut)
		}
		if page.GetEof() || len(page.Frames) == 0 {
			drained = true
			break
		}
		cursor = page.GetNextCursor()
	}
	if !drained {
		return "", "", sdkerrors.NewDaytonaError(
			fmt.Sprintf("process log replay exceeded %d pages before reaching the end of the log; use ProcessHandle.StreamLogs or page with ProcessHandle.Logs for logs this large", maxProcessLogPages),
			http.StatusRequestEntityTooLarge, nil)
	}
	stdoutTail, stderrTail := decoders.Flush()
	stdout.WriteString(stdoutTail)
	stderr.WriteString(stderrTail)
	return stdout.String(), stderr.String(), nil
}

func collectProcessStream(ctx context.Context, handle *ProcessHandle, runOpts *options.ProcessRun) (string, string, bool, error) {
	streamCtx := ctx
	cancel := func() {}
	if runOpts.WaitTimeoutMs != nil {
		streamCtx, cancel = context.WithTimeout(ctx, time.Duration(*runOpts.WaitTimeoutMs)*time.Millisecond)
	}
	defer cancel()
	events, err := handle.streamLogs(streamCtx, "start", "base64")
	if err != nil {
		return "", "", false, err
	}
	var stdout, stderr strings.Builder
	decoders := processOutputDecoders{}
	for event := range events {
		if event.Err != nil {
			if streamCtx.Err() != nil && ctx.Err() == nil {
				break
			}
			return "", "", false, event.Err
		}
		if event.Type == "eof" {
			break
		}
		if event.Frame == nil {
			continue
		}
		out, errOut, decodeErr := decoders.Decode(event.Frame)
		if decodeErr != nil {
			return "", "", false, decodeErr
		}
		stdout.WriteString(out)
		stderr.WriteString(errOut)
		if out != "" && runOpts.OnStdout != nil {
			runOpts.OnStdout(out)
		}
		if errOut != "" && runOpts.OnStderr != nil {
			runOpts.OnStderr(errOut)
		}
	}
	stdoutTail, stderrTail := decoders.Flush()
	stdout.WriteString(stdoutTail)
	stderr.WriteString(stderrTail)
	if stdoutTail != "" && runOpts.OnStdout != nil {
		runOpts.OnStdout(stdoutTail)
	}
	if stderrTail != "" && runOpts.OnStderr != nil {
		runOpts.OnStderr(stderrTail)
	}
	if ctx.Err() != nil {
		return "", "", false, ctx.Err()
	}
	timedOut := streamCtx.Err() == context.DeadlineExceeded
	return stdout.String(), stderr.String(), timedOut, nil
}
