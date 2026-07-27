// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package errors_test

import (
	stderrors "errors"
	"net/http"
	"testing"

	sdkerrors "github.com/daytona/clients/sdk-go/pkg/errors"
)

func TestDeprecatedTypedErrors_AsMatchesOriginalStatusSemantics(t *testing.T) {
	notFound := sdkerrors.NewDaytonaError("missing", http.StatusNotFound, nil)

	var nf *sdkerrors.DaytonaNotFoundError
	if !stderrors.As(notFound, &nf) {
		t.Fatalf("errors.As should match *DaytonaNotFoundError for a 404")
	}
	if nf.StatusCode != http.StatusNotFound || nf.Message != "missing" {
		t.Fatalf("populated wrapper lost fields: %+v", nf)
	}

	server := sdkerrors.NewDaytonaError("boom", http.StatusInternalServerError, nil)
	var nf2 *sdkerrors.DaytonaNotFoundError
	if stderrors.As(server, &nf2) {
		t.Fatalf("errors.As must NOT match *DaytonaNotFoundError for a 500")
	}

	var srv *sdkerrors.DaytonaServerError
	if !stderrors.As(server, &srv) {
		t.Fatalf("errors.As should match *DaytonaServerError for a 500")
	}
	if stderrors.As(notFound, &srv) {
		t.Fatalf("errors.As must NOT match *DaytonaServerError for a 404")
	}
}

func TestDeprecatedTypedErrors_AsCoversEveryLegacyType(t *testing.T) {
	cases := []struct {
		status  int
		matches func(err error) bool
	}{
		{http.StatusBadRequest, func(err error) bool {
			var e *sdkerrors.DaytonaValidationError
			return stderrors.As(err, &e)
		}},
		{http.StatusUnauthorized, func(err error) bool {
			var e *sdkerrors.DaytonaAuthenticationError
			return stderrors.As(err, &e)
		}},
		{http.StatusForbidden, func(err error) bool {
			var e *sdkerrors.DaytonaForbiddenError
			return stderrors.As(err, &e)
		}},
		{http.StatusConflict, func(err error) bool {
			var e *sdkerrors.DaytonaConflictError
			return stderrors.As(err, &e)
		}},
		{http.StatusTooManyRequests, func(err error) bool {
			var e *sdkerrors.DaytonaRateLimitError
			return stderrors.As(err, &e)
		}},
		{http.StatusRequestTimeout, func(err error) bool {
			var e *sdkerrors.DaytonaTimeoutError
			return stderrors.As(err, &e)
		}},
		{http.StatusGatewayTimeout, func(err error) bool {
			var e *sdkerrors.DaytonaTimeoutError
			return stderrors.As(err, &e)
		}},
	}
	for _, tc := range cases {
		matching := sdkerrors.NewDaytonaError("x", tc.status, nil)
		if !tc.matches(matching) {
			t.Fatalf("status %d should match its legacy typed error", tc.status)
		}
		other := sdkerrors.NewDaytonaError("x", http.StatusTeapot, nil)
		if tc.matches(other) {
			t.Fatalf("status 418 must not match the legacy typed error for %d", tc.status)
		}
	}
}

func TestDeprecatedTypedErrors_AsHookDoesNotAffectCanonicalTarget(t *testing.T) {
	err := sdkerrors.NewDaytonaError("missing", http.StatusNotFound, nil)
	var de *sdkerrors.DaytonaError
	if !stderrors.As(err, &de) {
		t.Fatalf("errors.As with *DaytonaError target must keep working")
	}
	if de != err {
		t.Fatalf("canonical target should be the original error instance")
	}
}

func TestDeprecatedConstructors_ProduceSentinelCompatibleErrors(t *testing.T) {
	var err error = sdkerrors.NewDaytonaNotFoundError("gone", nil)

	if !stderrors.Is(err, sdkerrors.ErrNotFound) {
		t.Fatalf("legacy constructor result should match ErrNotFound sentinel")
	}
	var de *sdkerrors.DaytonaError
	if !stderrors.As(err, &de) {
		t.Fatalf("legacy constructor result should unwrap to *DaytonaError")
	}
	if de.StatusCode != http.StatusNotFound {
		t.Fatalf("unexpected status: %d", de.StatusCode)
	}

	var srvErr error = sdkerrors.NewDaytonaServerError("boom", http.StatusBadGateway, nil)
	if !stderrors.Is(srvErr, sdkerrors.ErrBadGateway) {
		t.Fatalf("legacy server error should match its status sentinel")
	}
}
