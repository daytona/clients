// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/http"
	"strconv"
	"strings"
)

// Images manages reusable sandbox images. Obtain it with Client.Images.
// Its methods are safe for concurrent use.
type Images struct{ client *Client }

// Images returns the client's image service.
func (c *Client) Images() *Images { return &Images{client: c} }

// ImagePlatformFilter selects platform-provided images.
type ImagePlatformFilter string

const (
	// ImagePlatformOnly selects platform images.
	ImagePlatformOnly ImagePlatformFilter = "true"
	// ImagePlatformExclude selects project images.
	ImagePlatformExclude ImagePlatformFilter = "false"
	// ImagePlatformAny selects both kinds.
	ImagePlatformAny ImagePlatformFilter = "any"
)

// ListImagesOptions filters one page of images. Zero values use server defaults.
type ListImagesOptions struct {
	// Cursor is the opaque continuation cursor.
	Cursor string
	// Limit is 1–200; zero uses the server default.
	Limit int
	// Name filters by image name.
	Name string
	// Anonymous selects unnamed images; nil uses the server default (false).
	Anonymous *bool
	// SandboxID filters captures of this sandbox.
	SandboxID string
	// Platform selects platform or project images.
	Platform ImagePlatformFilter
}

// List returns one page, preferring eventually consistent search and falling
// back only on 404 or SEARCH_NOT_CONFIGURED. Get reads new images immediately.
func (s *Images) List(ctx context.Context, opts *ListImagesOptions) (*Page[*Image], error) {
	options := ListImagesOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	if err = artifactFilters(query, options.Name, options.SandboxID); err != nil {
		return nil, err
	}
	if options.Anonymous != nil {
		query.Set("anonymous", strconv.FormatBool(*options.Anonymous))
	}
	switch options.Platform {
	case "":
	case ImagePlatformOnly, ImagePlatformExclude, ImagePlatformAny:
		query.Set("platform", string(options.Platform))
	default:
		return nil, badRequest("platform must be true, false or any")
	}
	reader := artifactListReader{s.client, "/images", &s.client.imageSearchFallback}
	raw, err := reader.read(ctx, query)
	if err != nil {
		return nil, err
	}
	return artifactPage(raw, "images", parseImage)
}

// ListAll lazily visits all image pages, stopping on cancellation, a repeated
// cursor, or when the consumer breaks. Options are copied at the call.
func (s *Images) ListAll(ctx context.Context, opts *ListImagesOptions) iter.Seq2[*Image, error] {
	options := ListImagesOptions{}
	if opts != nil {
		options = *opts
		if opts.Anonymous != nil {
			value := *opts.Anonymous
			options.Anonymous = &value
		}
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[*Image], error) {
		pageOptions := options
		pageOptions.Cursor = cursor
		return s.List(ctx, &pageOptions)
	})
}

// Get fetches an image by ID or name. The reserved name "search" is refused;
// use its ID instead. With a wait, failed/error observations are returned,
// not raised; Pull and BuildAndWait raise ImageBuildFailedError instead.
func (s *Images) Get(ctx context.Context, id string, opts *GetArtifactOptions) (*Image, error) {
	path, err := artifactPath("/images", id)
	if err != nil {
		return nil, err
	}
	reader := artifactReader[*Image]{s.client, path, parseImage}
	return reader.get(ctx, opts)
}

// Delete deletes an image by ID or name. A missing image returns ErrNotFound.
// While a sandbox still uses the image it returns ErrConflict; this includes
// the few seconds after Sandbox.Delete returned, until the platform released
// the image, so retry a conflict after a moment.
func (s *Images) Delete(ctx context.Context, id string) error {
	segment, err := pathSegment(id)
	if err != nil {
		return err
	}
	return s.client.api().do(ctx, apiRequest{Method: http.MethodDelete, Path: "/images/" + segment})
}

// Promote names an anonymous image, making it permanent. It does not rename
// named images. Name conflicts and reserved prefixes are reported by the API.
func (s *Images) Promote(ctx context.Context, id, name string) (*Image, error) {
	segment, err := pathSegment(id)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(name) == "" {
		return nil, badRequest("name must be a non-empty string")
	}
	var raw JSONValue
	err = s.client.api().do(ctx, apiRequest{
		Method: http.MethodPatch, Path: "/images/" + segment,
		Body: struct {
			Name string `json:"name"`
		}{name}, Result: &raw,
	})
	if err != nil {
		return nil, err
	}
	return parseImage(raw)
}
