// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestImageR3BuildRejectsNamedIdleExpiry(t *testing.T) {
	for _, builder := range []*ImageBuilder{NewImageBuilder("alpine"), ImageFromDockerfile("FROM alpine")} {
		for _, expiry := range []time.Duration{time.Second, WaitForever} {
			t.Run(builder.base, func(t *testing.T) {
				// Given a named registry or inline build with anonymous retention.
				fake := newFakeServer(t, nil)
				client := newTestClient(t, fake.Server)
				options := &BuildImageOptions{Name: "saved", IdleExpiry: expiry}
				// When submitting the build.
				_, err := client.Images().Build(t.Context(), builder, options)
				// Then the conflicting options fail before any network request.
				require.ErrorIs(t, err, ErrBadRequest)
				require.Empty(t, fake.requests())
			})
		}
	}
}

func TestImageR3BuildRejectsBlankSeparatorName(t *testing.T) {
	for _, name := range []string{"\x1c", "\x1d", "\x1e", "\x1f"} {
		t.Run(name, func(t *testing.T) {
			// Given a name blank under the shared SDK whitespace rule.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When submitting a registry build.
			_, err := client.Images().Build(t.Context(), NewImageBuilder("alpine"), &BuildImageOptions{Name: name})
			// Then it is rejected locally.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestImageR3BuildOmitsDefaultSeparatorRegion(t *testing.T) {
	for _, region := range []string{"\x1c", "\x1d", "\x1e", "\x1f", "\x1cLoCaL\x1f"} {
		t.Run(region, func(t *testing.T) {
			// Given a blank or whitespace-padded local region.
			fake := newFakeServer(t, map[string]http.HandlerFunc{
				"POST /images":    jsonHandler(t, map[string]string{"id": "img"}),
				"GET /images/img": jsonHandler(t, imageFixture("img", "pending")),
			})
			client := newTestClient(t, fake.Server)
			// When submitting a registry build.
			_, err := client.Images().Build(t.Context(), NewImageBuilder("alpine"), &BuildImageOptions{Region: region})
			// Then the default placement is selected without sending region_id.
			require.NoError(t, err)
			require.JSONEq(t, `{"source":{"type":"registry","ref":"alpine"}}`, string(fake.requests()[0].Body))
		})
	}
}
