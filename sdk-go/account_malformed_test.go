// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"sort"
	"strconv"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

type accountFieldCase struct {
	path     []string
	optional bool
	nullable bool
	element  bool
}

func accountOptionalField(operation, path string) bool {
	switch operation {
	case "key":
		switch path {
		case "created_by", "expires_at", "last_used_at", "revoked_at", "key_prefix", "key_suffix",
			"service_account_id", "service_account_name":
			return true
		}
	case "budget":
		switch path {
		case "spent_cents", "evaluated_at", "exceeded_at", "stop_at":
			return true
		}
	case "limits":
		switch path {
		case "sandbox_resources", "mac_reservations", "members":
			return true
		}
	}
	return false
}

func accountNullableField(operation, path string) bool {
	if operation == "limits" {
		return strings.HasSuffix(path, ".limit")
	}
	if operation == "budget" {
		switch path {
		case "monthly_limit_cents", "period_start_date", "created_at", "updated_at":
			return true
		}
	}
	return false
}

func accountFieldCases(operation string, value any, prefix []string) []accountFieldCase {
	var cases []accountFieldCase
	switch typed := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			path := append(append([]string(nil), prefix...), key)
			name := strings.Join(path, ".")
			cases = append(cases, accountFieldCase{
				path: path, optional: accountOptionalField(operation, name), nullable: accountNullableField(operation, name),
			})
			cases = append(cases, accountFieldCases(operation, typed[key], path)...)
		}
	case []any:
		for i, item := range typed {
			path := append(append([]string(nil), prefix...), strconv.Itoa(i))
			cases = append(cases, accountFieldCase{path: path, element: true})
			cases = append(cases, accountFieldCases(operation, item, path)...)
		}
	}
	return cases
}

func accountMutateField(value any, path []string, mutation string) {
	name := path[0]
	if len(path) > 1 {
		switch typed := value.(type) {
		case map[string]any:
			accountMutateField(typed[name], path[1:], mutation)
		case []any:
			index, err := strconv.Atoi(name)
			if err != nil {
				panic(err) // Only generated fixture array indices reach this helper.
			}
			accountMutateField(typed[index], path[1:], mutation)
		}
		return
	}
	var replacement any
	if mutation == "type" {
		replacement = true
	}
	switch typed := value.(type) {
	case map[string]any:
		if mutation == "missing" {
			delete(typed, name)
		} else {
			typed[name] = replacement
		}
	case []any:
		index, err := strconv.Atoi(name)
		if err != nil {
			panic(err) // Only generated fixture array indices reach this helper.
		}
		typed[index] = replacement
	}
}

func TestAccountReadsRejectMalformedFieldsAtEveryDepth(t *testing.T) {
	for _, read := range accountTestReads() {
		for _, fieldCase := range accountFieldCases(read.name, read.fixture(), nil) {
			for _, mutation := range []string{"missing", "null", "type"} {
				if mutation == "missing" && (fieldCase.optional || fieldCase.element) {
					continue
				}
				if mutation == "null" && (fieldCase.optional || fieldCase.nullable) {
					continue
				}
				t.Run(read.name+"/"+strings.Join(fieldCase.path, ".")+"/"+mutation, func(t *testing.T) {
					// Given
					fixture := read.fixture()
					accountMutateField(fixture, fieldCase.path, mutation)
					fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
					client := newTestClient(t, fake.Server)
					// When
					err := read.call(t.Context(), client)
					// Then
					require.ErrorIs(t, err, ErrBadRequest)
					var sdkError *Error
					require.ErrorAs(t, err, &sdkError)
					require.Equal(t, "BAD_REQUEST", sdkError.Code)
					require.Equal(t, "SDK", sdkError.Source)
					require.Contains(t, sdkError.Message, fieldCase.path[len(fieldCase.path)-1])
				})
			}
		}
	}
}

func TestAccountReadsRejectNonObjectResponses(t *testing.T) {
	for _, read := range accountTestReads() {
		for i, fixture := range []any{nil, []any{}, "unexpected", true, 3} {
			t.Run(read.name+"/"+strconv.Itoa(i), func(t *testing.T) {
				// Given
				fake := newFakeServer(t, map[string]http.HandlerFunc{"GET " + read.path: jsonHandler(t, fixture)})
				client := newTestClient(t, fake.Server)
				// When
				err := read.call(t.Context(), client)
				// Then
				require.ErrorIs(t, err, ErrBadRequest)
			})
		}
	}
}
