// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestAccountReadsWireContract(t *testing.T) {
	for _, read := range accountTestReads() {
		t.Run(read.name, func(t *testing.T) {
			// Given
			fixture := read.fixture()
			fixture["future_field"] = map[string]any{"ignored": true}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			err := read.call(t.Context(), client)
			// Then
			require.NoError(t, err)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Equal(t, http.MethodGet, requests[0].Method)
			require.Equal(t, read.path, requests[0].Path)
			require.Empty(t, requests[0].Query)
			require.Empty(t, requests[0].Body)
			require.Equal(t, "Bearer test-key", requests[0].Header.Get("Authorization"))
			require.Equal(t, "sdk-go", requests[0].Header.Get("X-Daytona-Source"))
		})
	}
}

func TestAccountAuditMapsOpenActionNames(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /audit/actions": jsonHandler(t, accountAuditFixture())})
	client := newTestClient(t, fake.Server)
	// When
	result, err := client.ListAuditActions(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, &AuditActionList{Targets: []AuditActionGroup{
		{TargetType: "future_target", Actions: []string{"future_action", "create"}},
	}}, result)
}

func TestAccountCurrentAPIKeyMapsAllFields(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /api-keys/current": jsonHandler(t, accountKeyFixture())})
	client := newTestClient(t, fake.Server)
	stamp, err := time.Parse(time.RFC3339Nano, accountTestTimestamp)
	require.NoError(t, err)
	// When
	result, err := client.CurrentAPIKey(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, &ProjectAPIKey{
		ID: "key-id", Name: "automation", ProjectID: "project-id", ProjectPermissions: []string{"read:limits", "read:spending"},
		CreatedAt: stamp, CreatedBy: "creator-id", ExpiresAt: stamp, LastUsedAt: stamp, RevokedAt: stamp,
		KeyPrefix: "masked", KeySuffix: "display", ServiceAccountID: "service-id", ServiceAccountName: "automation",
		Context: ProjectAPIKeyContext{OrgID: "org-id", OrgName: "Organization", ProjectName: "Project"},
	}, result)
}

func TestAccountProjectBudgetMapsAllFields(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /usage/budget": jsonHandler(t, accountBudgetFixture())})
	client := newTestClient(t, fake.Server)
	stamp, err := time.Parse(time.RFC3339Nano, accountTestTimestamp)
	require.NoError(t, err)
	limit, spent := int64(12345), int64(42)
	// When
	result, err := client.ProjectBudget(t.Context())
	// Then
	require.NoError(t, err)
	require.Equal(t, &ProjectBudget{
		MonthlyLimitCents: &limit, PeriodStartDate: time.Date(2026, time.November, 1, 0, 0, 0, 0, time.UTC),
		SpentCents: &spent, CreatedAt: stamp, UpdatedAt: stamp, EvaluatedAt: stamp, ExceededAt: stamp, StopAt: stamp,
	}, result)
}

func TestAccountLimitsMapsAllFields(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /orgs/org-id/limits": jsonHandler(t, accountLimitsFixture())})
	client := newTestClient(t, fake.Server)
	count := int64(100)
	usage := UsageLimit{Limit: &count, Usage: 7}
	// When
	result, err := client.Limits(t.Context(), &GetLimitsOptions{OrganizationID: "org-id"})
	// Then
	require.NoError(t, err)
	require.Equal(t, &OrganizationLimits{
		Sandboxes: SandboxLimits{Concurrent: usage, Total: usage}, ImageBuilds: ImageBuildLimits{Concurrent: usage},
		SandboxTimeout:    SandboxTimeoutLimits{Max: time.Hour, Ceiling: 24 * time.Hour},
		Storage:           StorageLimits{Volumes: usage, Images: usage, Snapshots: usage},
		SecretsPerProject: usage, APIKeysPerProject: usage, APIKeysPerOrganization: usage,
		SandboxResources: &SandboxResourceLimits{MaxCores: 2.5, MaxMemoryMiB: 2048},
		MacReservations:  &usage, Members: &MemberLimits{UsageLimit: UsageLimit{Usage: 3}, PendingInvitations: 2},
	}, result)
}

func TestAccountReadsReturnServerErrors(t *testing.T) {
	for _, read := range accountTestReads() {
		t.Run(read.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusForbidden)
				writeJSON(t, w, map[string]any{"code": "ENTITLEMENT_REQUIRED", "source": "API", "message": "denied"})
			}})
			client := newTestClient(t, fake.Server)
			// When
			err := read.call(t.Context(), client)
			// Then
			require.ErrorIs(t, err, ErrEntitlementRequired)
			var sdkError *Error
			require.ErrorAs(t, err, &sdkError)
			require.Equal(t, "API", sdkError.Source)
			require.Equal(t, http.StatusForbidden, sdkError.StatusCode)
		})
	}
}

func TestAccountReadsHonorCancellation(t *testing.T) {
	for _, read := range accountTestReads() {
		t.Run(read.name, func(t *testing.T) {
			// Given
			started := make(chan struct{})
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: func(_ http.ResponseWriter, r *http.Request) {
				close(started)
				<-r.Context().Done()
			}})
			client := newTestClient(t, fake.Server)
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			done := make(chan error, 1)
			// When
			go func() { done <- read.call(ctx, client) }()
			<-started
			cancel()
			// Then
			require.ErrorIs(t, <-done, context.Canceled)
		})
	}
}

func TestAccountReadsRejectCanceledAndClosedClients(t *testing.T) {
	for _, read := range accountTestReads() {
		for _, condition := range []string{"canceled", "deadline", "closed"} {
			t.Run(read.name+"/"+condition, func(t *testing.T) {
				// Given
				fake := newFakeServer(t, nil)
				client := newTestClient(t, fake.Server)
				ctx, cancel := context.WithCancel(t.Context())
				defer cancel()
				want := context.Canceled
				switch condition {
				case "canceled":
					cancel()
				case "deadline":
					var deadlineCancel context.CancelFunc
					ctx, deadlineCancel = context.WithDeadline(t.Context(), time.Unix(1, 0))
					defer deadlineCancel()
					want = context.DeadlineExceeded
				case "closed":
					require.NoError(t, client.Close())
					want = ErrClientClosed
				}
				// When
				err := read.call(ctx, client)
				// Then
				require.ErrorIs(t, err, want)
				require.Empty(t, fake.requests())
			})
		}
	}
}
