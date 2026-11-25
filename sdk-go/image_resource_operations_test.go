// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestImagePromote_whenNameIsSearch(t *testing.T) {
	// Given an anonymous image addressed by the name reserved only for GET.
	wire := imageFixture("img", "ready")
	wire["name"], wire["anonymous"], wire["retention"] = "permanent", false, "permanent"
	current := imageFixture("img", "ready")
	current["name"], current["anonymous"], current["retention"] = "permanent", false, "permanent"
	current["region_ids"] = []string{"us", "eu"}
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"PATCH /images/search": jsonHandler(t, wire),
		"GET /images/img":      jsonHandler(t, current),
	})
	client := newTestClient(t, fake.Server)
	// When promoting.
	image, err := client.Images().Promote(t.Context(), "search", "permanent")
	// Then PATCH is permitted and the image is read back by the answered ID.
	require.NoError(t, err)
	require.Equal(t, "permanent", image.Name)
	require.False(t, image.Anonymous)
	require.Equal(t, ImageRetentionPermanent, image.Retention)
	require.Equal(t, []string{"us", "eu"}, image.AvailableIn)
	requests := fake.requests()
	require.Len(t, requests, 2)
	require.JSONEq(t, `{"name":"permanent"}`, string(requests[0].Body))
	require.Equal(t, http.MethodGet, requests[1].Method)
	require.Equal(t, "/images/img", requests[1].Path)
}

func TestImagePromote_whenAPIRejectsName(t *testing.T) {
	for _, tt := range []struct {
		code   string
		status int
		want   error
	}{
		{"IMAGE_NAME_TAKEN", 409, ErrImageNameTaken}, {"IMAGE_NAME_RESERVED", 400, ErrImageNameReserved},
	} {
		t.Run(tt.code, func(t *testing.T) {
			// Given the API's authoritative naming decision.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"PATCH /images/{id}": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(tt.status)
					writeJSON(t, w, map[string]string{"code": tt.code, "source": "API"})
				},
			})
			client := newTestClient(t, fake.Server)
			// When promoting an image with an escaped identifier.
			_, err := client.Images().Promote(t.Context(), "img/one", "img_reserved")
			// Then callers can match the exact server code.
			require.ErrorIs(t, err, tt.want)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, "API", detail.Source)
			require.Equal(t, "/images/img%2Fone", fake.requests()[0].Path)
		})
	}
}

func TestArtifactDelete_whenNamedSearchOrMissing(t *testing.T) {
	for _, resource := range []string{"images", "snapshots"} {
		for _, status := range []int{204, 404} {
			t.Run(resource+"/"+http.StatusText(status), func(t *testing.T) {
				// Given DELETE routes that do not conflict with search.
				fake := newFakeServer(t, map[string]http.HandlerFunc{
					"DELETE /" + resource + "/search": func(w http.ResponseWriter, _ *http.Request) {
						w.WriteHeader(status)
					},
				})
				client := newTestClient(t, fake.Server)
				remove := client.Images().Delete
				if resource == "snapshots" {
					remove = client.Snapshots().Delete
				}
				// When deleting.
				err := remove(t.Context(), "search")
				// Then success uses only DELETE, while missing remains an error.
				if status == 404 {
					require.ErrorIs(t, err, ErrNotFound)
				} else {
					require.NoError(t, err)
				}
				require.Len(t, fake.requests(), 1)
			})
		}
	}
}

func TestArtifactGet_whenNameIsSearch(t *testing.T) {
	for _, resource := range []string{"images", "snapshots"} {
		t.Run(resource, func(t *testing.T) {
			// Given a name that collides with the collection search route.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When looking it up by name.
			var err error
			if resource == "images" {
				_, err = client.Images().Get(t.Context(), "search", nil)
			} else {
				_, err = client.Snapshots().Get(t.Context(), "search", nil)
			}
			// Then only this GET operation refuses the reserved name locally.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestArtifactOperations_whenInputsAreInvalid(t *testing.T) {
	for _, tt := range []struct {
		name string
		call func(context.Context, *Client) error
	}{
		{"image get path", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Get(ctx, "..", nil)
			return err
		}},
		{"snapshot get path", func(ctx context.Context, c *Client) error {
			_, err := c.Snapshots().Get(ctx, "\xff", nil)
			return err
		}},
		{"image delete path", func(ctx context.Context, c *Client) error { return c.Images().Delete(ctx, ".") }},
		{"snapshot delete path", func(ctx context.Context, c *Client) error { return c.Snapshots().Delete(ctx, " ") }},
		{"promote name", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Promote(ctx, "img", " ")
			return err
		}},
		{"promote path", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Promote(ctx, "..", "valid")
			return err
		}},
		{"image wait duration", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Get(ctx, "img", &GetArtifactOptions{Timeout: -time.Second})
			return err
		}},
		{"snapshot wait duration", func(ctx context.Context, c *Client) error {
			_, err := c.Snapshots().Get(ctx, "snap", &GetArtifactOptions{Wait: true, Timeout: NoTimeout})
			return err
		}},
		{"pull duration", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Pull(ctx, "alpine", &PullImageOptions{Timeout: NoWait})
			return err
		}},
		{"pull reference", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Pull(ctx, "alpine\nRUN false", nil)
			return err
		}},
		{"pull image options", func(ctx context.Context, c *Client) error {
			_, err := c.Images().Pull(ctx, "alpine", &PullImageOptions{BuildImageOptions: BuildImageOptions{CPUArch: "other"}})
			return err
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given invalid public input and no HTTP handler.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When calling its resource API.
			err := tt.call(t.Context(), client)
			// Then validation returns the SDK's common error before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, SourceSDK, detail.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateSnapshot_whenNameIsBlank(t *testing.T) {
	// Given an unusable snapshot name.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)
	sandbox, err := client.newSandbox(sandboxInit{Info: SandboxInfo{ID: "sbx"}})
	require.NoError(t, err)
	// When creating a snapshot.
	_, err = sandbox.CreateSnapshot(t.Context(), " ")
	// Then it is rejected before submission.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestImagePromote_whenResponseIDCannotBeAPathID(t *testing.T) {
	for _, id := range []string{`..`, `\ud800`} {
		t.Run(id, func(t *testing.T) {
			// Given a promotion answer whose ID cannot be the read-back's path ID
			// (a lone-surrogate escape decodes to U+FFFD).
			wire, err := json.Marshal(imageFixture("img", "ready"))
			require.NoError(t, err)
			body := strings.Replace(string(wire), `"id":"img"`, `"id":"`+id+`"`, 1)
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"PATCH /images/img": func(w http.ResponseWriter, _ *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					_, writeErr := w.Write([]byte(body))
					assert.NoError(t, writeErr)
				},
			})
			client := newTestClient(t, fake.Server)
			// When promoting.
			_, err = client.Images().Promote(t.Context(), "img", "permanent")
			// Then the answer is refused before any read-back.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Len(t, fake.requests(), 1)
		})
	}
}
