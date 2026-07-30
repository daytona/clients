// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;
/**
 * Daemon could not read the requested file (code {@code FILE_READ_FAILED}, HTTP 500).
 *
 * <p>Subclass of {@link DaytonaInternalServerException}.
 */
public class DaytonaFileReadFailedException extends DaytonaInternalServerException {
    public DaytonaFileReadFailedException(String message) {
        super(message);
    }

    public DaytonaFileReadFailedException(String message, Throwable cause) {
        super(message, cause);
    }

    public DaytonaFileReadFailedException(String message, String code, String source) {
        super(message, code, source);
    }

    public DaytonaFileReadFailedException(String message, Throwable cause, String code, String source) {
        super(message, cause, code, source);
    }
}
