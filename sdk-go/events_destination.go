// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strings"
)

func (t *transport) learnSandboxEvents(spec apiRequest, response *http.Response, body []byte) {
	if t != &t.owner.apiClient.transport || spec.Method == "" || spec.Method == http.MethodGet {
		return
	}
	source, ok := t.owner.stateSource.(*eventsStateSource)
	if !ok {
		return
	}
	destination := response.Header.Get("X-Daytona-Cell")
	if destination == "" {
		return
	}
	var id string
	switch {
	case spec.Path == "/sandboxes", spec.Path == "/sandboxes/from-snapshot", spec.Path == "/sandboxes/macos",
		strings.HasPrefix(spec.Path, "/sandboxes/") && strings.HasSuffix(spec.Path, "/fork"):
		var created struct {
			ID string `json:"id"`
		}
		if json.Unmarshal(body, &created) != nil {
			return
		}
		id = created.ID
	case spec.Path == "/sandboxes/batch-wait":
		return
	case strings.HasPrefix(spec.Path, "/sandboxes/"):
		segment, _, _ := strings.Cut(strings.TrimPrefix(spec.Path, "/sandboxes/"), "/")
		decoded, err := url.PathUnescape(segment)
		if err != nil {
			return
		}
		id = decoded
	default:
		return
	}
	source.hub.Learn(regionOf(id), destination)
}
