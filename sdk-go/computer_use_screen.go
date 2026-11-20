// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/base64"
	"net/url"
	"strconv"
)

// ComputerUseScreen captures the desktop screen.
type ComputerUseScreen struct{ s *Sandbox }

// ComputerUseScreenshotFormat selects screenshot encoding.
type ComputerUseScreenshotFormat string

const (
	// ComputerUseScreenshotUnknown is an unrecognized format.
	ComputerUseScreenshotUnknown ComputerUseScreenshotFormat = "unknown"
	// ComputerUseScreenshotPNG selects PNG encoding.
	ComputerUseScreenshotPNG ComputerUseScreenshotFormat = "png"
	// ComputerUseScreenshotJPEG selects JPEG encoding.
	ComputerUseScreenshotJPEG ComputerUseScreenshotFormat = "jpeg"
)

// ComputerUseRegion is a desktop rectangle in pixels.
type ComputerUseRegion struct {
	// X is the left edge in pixels.
	X int
	// Y is the top edge in pixels.
	Y int
	// Width is the horizontal extent in pixels.
	Width int
	// Height is the vertical extent in pixels.
	Height int
}

// ComputerUseScreenshotOptions selects the captured region and encoding.
type ComputerUseScreenshotOptions struct {
	// Region defaults to the entire screen.
	Region *ComputerUseRegion
	// Format selects compression; empty leaves the server default.
	Format ComputerUseScreenshotFormat
	// Quality selects compression quality. Nil leaves the server default.
	Quality *int
	// Scale selects the image scale factor. Nil leaves the server default.
	Scale *float64
	// ShowCursor controls cursor rendering; nil leaves the server default.
	ShowCursor *bool
}

// ComputerUseScreenshot is a captured image with server-reported metadata.
type ComputerUseScreenshot struct {
	// Data is the decoded image, not base64 text.
	Data []byte
	// SizeBytes is the server's image size in bytes.
	SizeBytes int64
	// CursorPosition is absent when not reported.
	CursorPosition *ComputerUsePoint
}

// Screenshot captures the entire screen or a region. Setting Format, Quality or
// Scale selects the compressed capture endpoint. The server validates encoding
// options. Malformed base64 in a successful response returns ErrBadRequest.
func (s *ComputerUseScreen) Screenshot(
	ctx context.Context, opts *ComputerUseScreenshotOptions,
) (*ComputerUseScreenshot, error) {
	spec := toolboxRequest{Path: "/computeruse/screenshot", Query: make(url.Values)}
	options := ComputerUseScreenshotOptions{}
	if opts != nil {
		options = *opts
	}
	if options.Region != nil {
		spec.Path += "/region"
		spec.Query.Set("x", strconv.Itoa(options.Region.X))
		spec.Query.Set("y", strconv.Itoa(options.Region.Y))
		spec.Query.Set("width", strconv.Itoa(options.Region.Width))
		spec.Query.Set("height", strconv.Itoa(options.Region.Height))
	}
	if options.Format != "" || options.Quality != nil || options.Scale != nil {
		spec.Path += "/compressed"
	}
	if options.Format != "" {
		spec.Query.Set("format", string(options.Format))
	}
	if options.Quality != nil {
		spec.Query.Set("quality", strconv.Itoa(*options.Quality))
	}
	if options.Scale != nil {
		spec.Query.Set("scale", strconv.FormatFloat(*options.Scale, 'f', -1, 64))
	}
	if options.ShowCursor != nil {
		spec.Query.Set("show_cursor", strconv.FormatBool(*options.ShowCursor))
	}
	raw, err := desktopRead(ctx, s.s, spec)
	if err != nil {
		return nil, err
	}
	d := decodeDesktop(raw)
	data, decodeErr := base64.StdEncoding.DecodeString(d.text("screenshot"))
	if decodeErr != nil {
		d.check(badRequest("response field screenshot must contain base64 image bytes"))
	}
	result := &ComputerUseScreenshot{Data: data, SizeBytes: d.nonnegativeInteger("sizeBytes")}
	if present(d.object["cursorPosition"]) {
		cursor, parseErr := parseDesktopPoint(d.object["cursorPosition"])
		d.check(parseErr)
		result.CursorPosition = cursor
	}
	return result, d.err
}
