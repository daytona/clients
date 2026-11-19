// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;

import io.daytona.sdk.model.ExecuteResponse;
import java.util.Collections;
import java.util.Map;
import java.util.TreeMap;

/** Rank-specific failures and successful results from a parallel node operation. */
public final class DaytonaNodeOperationException extends DaytonaException {
    private final Map<Integer, Throwable> failures;
    private final Map<Integer, ExecuteResponse> results;

    public DaytonaNodeOperationException(Map<Integer, Throwable> failures, Map<Integer, ExecuteResponse> results) {
        super("Sandbox node operations failed for ranks " + new TreeMap<>(failures).keySet());
        this.failures = Collections.unmodifiableMap(new TreeMap<>(failures));
        this.results = Collections.unmodifiableMap(new TreeMap<>(results));
        failures.values().forEach(this::addSuppressed);
    }

    public Map<Integer, Throwable> getFailures() { return failures; }
    public Map<Integer, ExecuteResponse> getResults() { return results; }
}
