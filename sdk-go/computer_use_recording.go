// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"math"
	"net/http"
	"time"
)

// ComputerUseRecordings manages desktop recordings.
type ComputerUseRecordings struct{ s *Sandbox }

// ComputerUseRecording describes a recording stored in the sandbox.
type ComputerUseRecording struct {
	// ID identifies the recording.
	ID string
	// FileName is its file name.
	FileName string
	// FilePath is its absolute path in the sandbox.
	FilePath string
	// StartTime is when recording started.
	StartTime time.Time
	// EndTime is zero until reported.
	EndTime time.Time
	// Status is the server's open recording status.
	Status string
	// Duration is absent until reported.
	Duration *time.Duration
	// SizeBytes is absent until reported.
	SizeBytes *int64
}

// ComputerUseRecordingStartOptions labels a recording.
type ComputerUseRecordingStartOptions struct {
	// Label is omitted when nil; a pointer to "" sends an empty label.
	Label *string `json:"label,omitempty"`
}

// ComputerUseRecordingDownloadOptions controls the in-memory recording limit.
type ComputerUseRecordingDownloadOptions struct {
	// MaxResponseBytes replaces the client's cap; zero keeps the client default,
	// NoLimit disables it. Other negative values return ErrBadRequest.
	MaxResponseBytes int64
}

// Start starts recording. Nil options leave the label unset.
func (s *ComputerUseRecordings) Start(
	ctx context.Context, opts *ComputerUseRecordingStartOptions,
) (*ComputerUseRecording, error) {
	body := ComputerUseRecordingStartOptions{}
	if opts != nil {
		body = *opts
	}
	raw, err := desktopRead(ctx, s.s, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/recordings/start", Body: body,
	})
	if err != nil {
		return nil, err
	}
	return parseDesktopRecording(raw)
}

// Stop stops the specified recording.
func (s *ComputerUseRecordings) Stop(ctx context.Context, id string) (*ComputerUseRecording, error) {
	body := struct {
		ID string `json:"id"`
	}{ID: id}
	raw, err := desktopRead(ctx, s.s, toolboxRequest{
		Method: http.MethodPost, Path: "/computeruse/recordings/stop", Body: body,
	})
	if err != nil {
		return nil, err
	}
	return parseDesktopRecording(raw)
}

// List returns every recording. The server does not paginate this list.
func (s *ComputerUseRecordings) List(ctx context.Context) ([]ComputerUseRecording, error) {
	raw, err := desktopRead(ctx, s.s, toolboxRequest{Path: "/computeruse/recordings"})
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	rows := desktopField[[]JSONValue](d, "recordings", true)
	result := make([]ComputerUseRecording, 0, len(rows))
	for _, row := range rows {
		recording, parseErr := parseDesktopRecording(row)
		if parseErr != nil {
			return nil, parseErr
		}
		result = append(result, *recording)
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}

// Get reads one recording. Invalid path IDs return ErrBadRequest.
func (s *ComputerUseRecordings) Get(ctx context.Context, id string) (*ComputerUseRecording, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return nil, err
	}
	raw, err := desktopRead(ctx, s.s, toolboxRequest{Path: "/computeruse/recordings/" + segment})
	if err != nil {
		return nil, err
	}
	return parseDesktopRecording(raw)
}

// Delete removes a recording. Invalid path IDs return ErrBadRequest.
func (s *ComputerUseRecordings) Delete(ctx context.Context, id string) error {
	segment, err := pathSegment(id)
	if err != nil {
		return err
	}
	return s.s.toolbox().do(ctx, toolboxRequest{
		Method: http.MethodDelete, Path: "/computeruse/recordings/" + segment,
	})
}

// Download buffers a recording in memory. Transfers have no total deadline, but
// the client's request timeout bounds idle gaps. The response cap is enforced
// while reading; oversized bodies return ErrPayloadTooLarge.
func (s *ComputerUseRecordings) Download(
	ctx context.Context, id string, opts *ComputerUseRecordingDownloadOptions,
) ([]byte, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return nil, err
	}
	limit := int64(0)
	if opts != nil {
		limit = opts.MaxResponseBytes
	}
	if limit < NoLimit || limit > maxSafeInteger {
		return nil, badRequest("MaxResponseBytes must be NoLimit, zero, or a positive safe integer")
	}
	var data []byte
	err = s.s.toolbox().do(ctx, toolboxRequest{
		Path:     "/computeruse/recordings/" + segment + "/download",
		Transfer: true, MaxResponseBytes: limit, ResponseBody: &data,
	})
	return data, err
}

func parseDesktopRecording(raw JSONValue) (*ComputerUseRecording, error) {
	d := decodeDesktop(raw)
	result := &ComputerUseRecording{
		ID: d.text("id"), FileName: d.text("fileName"), FilePath: d.text("filePath"),
		StartTime: d.timestamp("startTime", true), EndTime: d.timestamp("endTime", false), Status: d.text("status"),
	}
	if present(d.object["durationSeconds"]) {
		seconds := desktopField[float64](d, "durationSeconds", false)
		nanoseconds := seconds * float64(time.Second)
		if math.IsInf(nanoseconds, 0) || nanoseconds < math.MinInt64 || nanoseconds >= -float64(math.MinInt64) {
			d.check(badRequest("response field durationSeconds overflows time.Duration"))
		} else {
			duration := time.Duration(nanoseconds)
			result.Duration = &duration
		}
	}
	if present(d.object["sizeBytes"]) {
		size := d.integer("sizeBytes", [2]int64{math.MinInt64, math.MaxInt64})
		result.SizeBytes = &size
	}
	if d.err != nil {
		return nil, d.err
	}
	return result, nil
}
