// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestPlatformValidation_whenInvalidInputNamesGoField(t *testing.T) {
	for _, tt := range []struct {
		name string
		call func(*Client) error
		want []string
	}{
		{"image readiness", func(c *Client) error {
			_, err := c.Images().Get(t.Context(), "image", &GetArtifactOptions{Timeout: -time.Second})
			return err
		}, []string{"Timeout"}},
		{"snapshot readiness", func(c *Client) error {
			_, err := c.Snapshots().Get(t.Context(), "snapshot", &GetArtifactOptions{Timeout: -time.Second})
			return err
		}, []string{"Timeout"}},
		{"pull readiness", func(c *Client) error {
			_, err := c.Images().Pull(t.Context(), "alpine", &PullImageOptions{Timeout: -time.Second})
			return err
		}, []string{"Timeout"}},
		{"build readiness", func(c *Client) error {
			_, err := c.Images().BuildAndWait(t.Context(), NewImageBuilder("alpine"),
				&BuildImageWaitOptions{Timeout: -time.Second})
			return err
		}, []string{"Timeout"}},
		{"context submission timeout", func(c *Client) error {
			_, err := c.Images().BuildFromContext(t.Context(), "unused", &BuildFromContextOptions{Timeout: -time.Second})
			return err
		}, []string{"Timeout"}},
		{"context Dockerfile", func(c *Client) error {
			_, err := c.Images().BuildFromContext(t.Context(), "unused", &BuildFromContextOptions{Dockerfile: " "})
			return err
		}, []string{"Dockerfile"}},
		{"reader Dockerfile", func(c *Client) error {
			_, err := c.Images().BuildFromContextReader(t.Context(), strings.NewReader("unused"), 6,
				&BuildFromContextOptions{Dockerfile: " "})
			return err
		}, []string{"Dockerfile"}},
		{"build log cursor", func(c *Client) error {
			for _, err := range c.Images().FollowBuildLogs(t.Context(), "image", &FollowBuildLogsOptions{Cursor: "\n"}) {
				return err
			}
			return nil
		}, []string{"Cursor"}},
		{"build log timeout", func(c *Client) error {
			for _, err := range c.Images().FollowBuildLogs(t.Context(), "image",
				&FollowBuildLogsOptions{Timeout: -time.Second}) {
				return err
			}
			return nil
		}, []string{"Timeout"}},
		{"reservation region", func(c *Client) error {
			_, err := c.Reservations().Create(t.Context(), "\xff", nil)
			return err
		}, []string{"Region", "Model"}},
		{"reservation model", func(c *Client) error {
			_, err := c.Reservations().CreateBulk(t.Context(), "region", 1,
				&CreateReservationsBulkOptions{Model: "\xff"})
			return err
		}, []string{"Region", "Model"}},
		{"page limit", func(c *Client) error {
			_, err := c.Images().List(t.Context(), &ListImagesOptions{Limit: -1})
			return err
		}, []string{"Limit"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			// Given invalid caller input and no usable API routes.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When calling the public operation.
			err := tt.call(client)
			// Then local validation identifies the Go options without a request.
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.True(t, strings.HasPrefix(detail.Message, tt.want[0]), "message: %s", detail.Message)
			for _, name := range tt.want {
				require.ErrorContains(t, err, name)
			}
			require.Empty(t, fake.requests())
		})
	}
}

func TestReservations_whenInvalidMinCountExplainsDefault(t *testing.T) {
	for _, minCount := range []int{-1, 3} {
		t.Run("outside range", func(t *testing.T) {
			// Given a minimum outside the accepted zero-or-1-to-count range.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When attempting a bulk purchase.
			_, err := client.Reservations().CreateBulk(t.Context(), "region", 2,
				&CreateReservationsBulkOptions{MinCount: minCount})
			// Then the diagnostic includes zero's defaulting semantics.
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, "MinCount must be 0 (defaults to count) or 1 to count")
			require.Empty(t, fake.requests())
		})
	}
}
