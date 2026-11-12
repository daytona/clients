# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The error hierarchy shared by every API version.

``except daytona.DaytonaNotFoundError`` catches a not-found error from
``Daytona().v0`` and from ``Daytona().v2`` alike: both namespaces' classes
inherit these markers (v2 in its source, v0 through the publish-time bundle
step, scripts/bundle_v0_python.py). The markers carry no state and no
``__init__``; each namespace's own classes keep their attributes (``code``,
``status_code``, ...). The categories are the ones both v0 and v2 have.
"""

from __future__ import annotations

from typing import Optional


class DaytonaError(Exception):
    """Base of every error the SDK raises, in either namespace.

    The fields below are set by both namespaces' error classes (v0's and
    v2's); they are annotated here, without values, so code catching the root
    class type-checks. A bare marker instance does not carry them.
    """

    message: str
    status_code: Optional[int]
    code: Optional[str]
    source: Optional[str]


class DaytonaBadRequestError(DaytonaError):
    """The request was rejected as invalid (HTTP 400 and client-side validation)."""


class DaytonaAuthenticationError(DaytonaError):
    """Missing or invalid credentials (HTTP 401)."""


class DaytonaForbiddenError(DaytonaError):
    """The credentials do not allow the operation (HTTP 403)."""


class DaytonaNotFoundError(DaytonaError):
    """The resource does not exist (HTTP 404)."""


class DaytonaTimeoutError(DaytonaError):
    """A request or wait timed out, on the server (HTTP 408) or in the client."""


class DaytonaConflictError(DaytonaError):
    """The resource's state does not allow the operation (HTTP 409)."""


class DaytonaGoneError(DaytonaError):
    """The resource is gone for good (HTTP 410)."""


class DaytonaUnprocessableEntityError(DaytonaError):
    """The request was well-formed but could not be processed (HTTP 422)."""


class DaytonaRateLimitError(DaytonaError):
    """Too many requests, or a quota was exceeded (HTTP 429)."""


class DaytonaInternalServerError(DaytonaError):
    """The server failed (HTTP 500)."""


class DaytonaServiceUnavailableError(DaytonaError):
    """The service is temporarily unavailable (HTTP 503)."""


class DaytonaConnectionError(DaytonaError):
    """The API could not be reached."""


SHARED_ERRORS = (
    "DaytonaError",
    "DaytonaBadRequestError",
    "DaytonaAuthenticationError",
    "DaytonaForbiddenError",
    "DaytonaNotFoundError",
    "DaytonaTimeoutError",
    "DaytonaConflictError",
    "DaytonaGoneError",
    "DaytonaUnprocessableEntityError",
    "DaytonaRateLimitError",
    "DaytonaInternalServerError",
    "DaytonaServiceUnavailableError",
    "DaytonaConnectionError",
)
