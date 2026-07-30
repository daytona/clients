// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;
/**
 * Supplied file path was rejected by the daemon (code {@code INVALID_FILE_PATH}, HTTP 400).
 *
 * <p>Subclass of {@link DaytonaBadRequestException}.
 */
public class DaytonaInvalidFilePathException extends DaytonaBadRequestException {
    public DaytonaInvalidFilePathException(String message) {
        super(message);
    }

    public DaytonaInvalidFilePathException(String message, Throwable cause) {
        super(message, cause);
    }

    public DaytonaInvalidFilePathException(String message, String code, String source) {
        super(message, code, source);
    }

    public DaytonaInvalidFilePathException(String message, Throwable cause, String code, String source) {
        super(message, cause, code, source);
    }
}
