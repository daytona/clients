// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;

/**
 * Raised for semantic validation failures (HTTP 422).
 *
 * <p>The mapper throws this subclass for 422 responses so that pre-existing
 * {@code catch (DaytonaValidationException e)} blocks keep matching, while
 * {@code catch (DaytonaUnprocessableEntityException e)} also matches via the
 * parent class.
 *
 * <p>Exists for backward compatibility only. Deleting this class (and
 * switching the 422 case in {@code ExceptionMapper} back to the parent) is
 * the whole removal.
 *
 * @deprecated Use {@link DaytonaUnprocessableEntityException} instead.
 */
@Deprecated
public class DaytonaValidationException extends DaytonaUnprocessableEntityException {
    /**
     * Creates a validation exception.
     *
     * @param message error description
     */
    public DaytonaValidationException(String message) {
        super(message);
    }

    /**
     * Creates a validation exception with a cause.
     *
     * @param message error description
     * @param cause root cause
     */
    public DaytonaValidationException(String message, Throwable cause) {
        super(message, cause);
    }

    public DaytonaValidationException(String message, String code, String source) {
        super(message, code, source);
    }

    public DaytonaValidationException(String message, Throwable cause, String code, String source) {
        super(message, cause, code, source);
    }
}
