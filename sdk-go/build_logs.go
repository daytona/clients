// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/url"
	"strings"
)

// BuildLogs returns a one-shot text snapshot, bounded to 2 MiB. ErrNotFound
// means no image build was found. The client's request timeout bounds the
// entire read, including discovery; NoTimeout leaves only the context bound.
func (s *Sandbox) BuildLogs(ctx context.Context) (*BuildLogsSnapshot, error) {
	path, err := buildLogDiscoveryPath("sandboxes", s.id)
	if err != nil {
		return nil, err
	}
	readCtx, scope := newRequestScope(ctx, s.client, s.client.config.timeout)
	defer scope.close()
	scope.kick()
	target, err := discoverBuildLogs(readCtx, s.client.api(), path)
	if err != nil {
		return nil, err
	}
	spec := apiRequest{
		Path: target.path, Query: url.Values{"follow": {"false"}, "format": {"text"}},
		MaxResponseBytes: buildLogMaxEvent,
	}
	data, err := target.readFinite(readCtx, spec, buildLogMaxSnapshot)
	if err != nil {
		return nil, err
	}
	return &BuildLogsSnapshot{Text: buildLogText(data)}, nil
}

// EntrypointLogs returns the sandbox entrypoint's retained plain-text log.
// The client's request timeout and buffered response limit apply.
func (s *Sandbox) EntrypointLogs(ctx context.Context) (string, error) {
	segment, err := pathSegment(s.id)
	if err != nil {
		return "", err
	}
	var body []byte
	if requestErr := s.client.api().do(ctx, apiRequest{
		Path: "/sandboxes/" + segment + "/logs", ResponseBody: &body,
	}); requestErr != nil {
		return "", requestErr
	}
	return buildLogText(body), nil
}

// FollowBuildLogs observes this sandbox's build lazily. Unknown events are
// skipped. A discovery HTTP error is returned unchanged; subsequent failures
// yield BuildLogInterruptedError with the last delivered cursor. Broken streams
// reconnect up to five times without progress. Breaking iteration closes the
// response immediately. Context cancellation and Client.Close stop observation.
func (s *Sandbox) FollowBuildLogs(ctx context.Context, opts *FollowBuildLogsOptions) iter.Seq2[BuildLogEvent, error] {
	return followBuildLogs(ctx, buildLogSource{s.client, "sandboxes", s.id}, opts)
}

// FollowBuildLogs observes an image build lazily, with the same checkpoint,
// retry and cancellation semantics as Sandbox.FollowBuildLogs. imageID is the
// image's ID or name; the reserved name "search" is refused with
// ErrBadRequest before any request (follow such an image by its ID).
func (s *Images) FollowBuildLogs(ctx context.Context, imageID string,
	opts *FollowBuildLogsOptions,
) iter.Seq2[BuildLogEvent, error] {
	return followBuildLogs(ctx, buildLogSource{s.client, "images", imageID}, opts)
}

type buildLogSource struct {
	client     *Client
	collection string
	id         string
}

func buildLogDiscoveryPath(collection, id string) (string, error) {
	if collection == "images" && id == "search" {
		// GET /images/search is the search route: the name cannot be resolved.
		return "", badRequest("'search' cannot be looked up by name; use the resource ID")
	}
	segment, err := pathSegment(id)
	if err != nil {
		return "", err
	}
	return "/" + collection + "/" + segment + "/build-logs-url", nil
}

func buildLogText(data []byte) string {
	// Decode invalid UTF-8 using maximal-subpart replacement, retaining a BOM.
	// The short incomplete tail is one replacement, as in TextDecoder.
	var text strings.Builder
	for len(data) > 0 {
		n := buildLogTextWidth(data)
		if n == 0 {
			text.WriteRune('\uFFFD')
			data = data[1:]
		} else if n < 0 {
			text.WriteRune('\uFFFD')
			data = data[-n:]
		} else {
			text.Write(data[:n])
			data = data[n:]
		}
	}
	return text.String()
}
