// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestAccountReadsAcceptAbsentAndNullOptionalFields(t *testing.T) {
	for _, read := range accountTestReads() {
		for _, fieldCase := range accountFieldCases(read.name, read.fixture(), nil) {
			mutations := []string{}
			if fieldCase.optional {
				mutations = append(mutations, "missing", "null")
			} else if fieldCase.nullable {
				mutations = append(mutations, "null")
			}
			for _, mutation := range mutations {
				t.Run(read.name+"/"+strings.Join(fieldCase.path, ".")+"/"+mutation, func(t *testing.T) {
					// Given
					fixture := read.fixture()
					accountMutateField(fixture, fieldCase.path, mutation)
					fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
					client := newTestClient(t, fake.Server)
					// When
					err := read.call(t.Context(), client)
					// Then
					require.NoError(t, err)
				})
			}
		}
	}
}

func TestAccountBudgetUnsetAndZeroValues(t *testing.T) {
	for _, unset := range []bool{true, false} {
		t.Run(strconv.FormatBool(unset), func(t *testing.T) {
			// Given
			fixture := map[string]any{
				"monthly_limit_cents": nil, "period_start_date": nil, "created_at": nil, "updated_at": nil,
			}
			if !unset {
				fixture["monthly_limit_cents"] = 0
				fixture["spent_cents"] = 0
			}
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /usage/budget": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			budget, err := client.ProjectBudget(t.Context())
			// Then
			require.NoError(t, err)
			require.True(t, budget.PeriodStartDate.IsZero())
			if unset {
				require.Nil(t, budget.MonthlyLimitCents)
				require.Nil(t, budget.SpentCents)
			} else {
				require.NotNil(t, budget.MonthlyLimitCents)
				require.Zero(t, *budget.MonthlyLimitCents)
				require.NotNil(t, budget.SpentCents)
				require.Zero(t, *budget.SpentCents)
			}
		})
	}
}

func TestAccountReadsRejectMalformedTimestamps(t *testing.T) {
	for _, read := range accountTestReads() {
		for name := range read.fixture() {
			if !strings.HasSuffix(name, "_at") {
				continue
			}
			t.Run(read.name+"/"+name, func(t *testing.T) {
				// Given
				fixture := read.fixture()
				fixture[name] = "2026-02-30T25:00:00Z"
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
				client := newTestClient(t, fake.Server)
				// When
				err := read.call(t.Context(), client)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Contains(t, err.Error(), name)
			})
		}
	}
}

func TestAccountBudgetCalendarDates(t *testing.T) {
	for _, date := range []string{"2026-02-29", "0000-01-01", "2026-1-01", "2026-01-1", "2026-01-01T00:00:00Z", "", "2026-13-01"} {
		t.Run(date, func(t *testing.T) {
			// Given
			fixture := accountBudgetFixture()
			fixture["period_start_date"] = date
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /usage/budget": jsonHandler(t, fixture)})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.ProjectBudget(t.Context())
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Contains(t, err.Error(), "period_start_date")
		})
	}
}

func TestAccountReadsRejectFractionalIntegers(t *testing.T) {
	for _, operation := range []string{"budget", "limits"} {
		read := accountReadByName(t, operation)
		for _, fieldCase := range accountFieldCases(read.name, read.fixture(), nil) {
			name := fieldCase.path[len(fieldCase.path)-1]
			switch name {
			case "monthly_limit_cents", "spent_cents", "usage", "limit", "pending_invitations", "max_memory_mib":
			default:
				continue
			}
			t.Run(operation+"/"+strings.Join(fieldCase.path, "."), func(t *testing.T) {
				// Given
				fixture := read.fixture()
				target := fixture
				for _, part := range fieldCase.path[:len(fieldCase.path)-1] {
					nested, ok := target[part].(map[string]any)
					require.True(t, ok)
					target = nested
				}
				target[name] = 1.5
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
				client := newTestClient(t, fake.Server)
				// When
				err := read.call(t.Context(), client)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
				require.Contains(t, err.Error(), name)
			})
		}
	}
}
