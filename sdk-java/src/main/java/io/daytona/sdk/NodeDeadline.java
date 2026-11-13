// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaTimeoutException;
import java.util.concurrent.TimeUnit;
import okhttp3.Call;

/** A monotonic budget shared by refresh, submission, polling and final refresh. */
final class NodeDeadline {
    private final long started = System.nanoTime();
    private final long limit;

    NodeDeadline(long seconds) {
        if (seconds < 0 || seconds > Long.MAX_VALUE / 1_000_000_000L) {
            throw new DaytonaException("Timeout must be a non-negative number of seconds");
        }
        limit = seconds * 1_000_000_000L;
    }

    long remainingMillis() {
        check();
        return limit == 0 ? 0 : Math.max(1, TimeUnit.NANOSECONDS.toMillis(remainingNanos()));
    }

    private long remainingNanos() { return limit - (System.nanoTime() - started); }

    void check() {
        if (Thread.currentThread().isInterrupted()) {
            throw new DaytonaTimeoutException("Sandbox node operation interrupted");
        }
        if (limit > 0 && remainingNanos() <= 0) {
            throw new DaytonaTimeoutException("Sandbox node operation exceeded its timeout");
        }
    }

    void pause(long millis) {
        check();
        long nanos = TimeUnit.MILLISECONDS.toNanos(millis);
        if (limit > 0) nanos = Math.min(nanos, remainingNanos());
        try {
            TimeUnit.NANOSECONDS.sleep(nanos);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        check();
    }

    private void bound(Call call) {
        check();
        if (limit > 0) call.timeout().timeout(remainingNanos(), TimeUnit.NANOSECONDS);
    }

    private <T> T await(Call call, java.util.concurrent.CompletableFuture<T> result) {
        try {
            T value = limit == 0 ? result.get() : result.get(remainingNanos(), TimeUnit.NANOSECONDS);
            check();
            return value;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new DaytonaTimeoutException("Sandbox node operation interrupted", e);
        } catch (java.util.concurrent.TimeoutException e) {
            throw new DaytonaTimeoutException("Sandbox node operation exceeded its timeout", e);
        } catch (java.util.concurrent.ExecutionException e) {
            check();
            Throwable cause = e.getCause();
            if (cause instanceof io.daytona.api.client.ApiException) {
                return ExceptionMapper.callMain(() -> { throw (io.daytona.api.client.ApiException) cause; });
            }
            if (cause instanceof io.daytona.toolbox.client.ApiException) {
                return ExceptionMapper.callToolbox(() -> { throw (io.daytona.toolbox.client.ApiException) cause; });
            }
            if (cause instanceof RuntimeException) throw (RuntimeException) cause;
            throw new DaytonaException("Sandbox node request failed", cause);
        } finally {
            // Interrupting the SDK wait must also abort the HTTP request before
            // the task enters its independent session-cleanup budget.
            if (!result.isDone()) call.cancel();
        }
    }

    <T> T main(io.daytona.api.client.ApiClient client,
               ExceptionMapper.MainSupplier<Call> factory, Class<T> type) {
        Call call = ExceptionMapper.callMain(factory);
        bound(call);
        java.util.concurrent.CompletableFuture<T> result = new java.util.concurrent.CompletableFuture<>();
        client.executeAsync(call, type, new io.daytona.api.client.ApiCallback<T>() {
            public void onSuccess(T value, int code, java.util.Map<String, java.util.List<String>> headers) { result.complete(value); }
            public void onFailure(io.daytona.api.client.ApiException e, int code, java.util.Map<String, java.util.List<String>> headers) { result.completeExceptionally(e); }
            public void onUploadProgress(long written, long length, boolean done) {}
            public void onDownloadProgress(long read, long length, boolean done) {}
        });
        return await(call, result);
    }

    <T> T toolbox(io.daytona.toolbox.client.ApiClient client,
                  ExceptionMapper.ToolboxSupplier<Call> factory, Class<T> type) {
        Call call = ExceptionMapper.callToolbox(factory);
        bound(call);
        java.util.concurrent.CompletableFuture<T> result = new java.util.concurrent.CompletableFuture<>();
        client.executeAsync(call, type, new io.daytona.toolbox.client.ApiCallback<T>() {
            public void onSuccess(T value, int code, java.util.Map<String, java.util.List<String>> headers) { result.complete(value); }
            public void onFailure(io.daytona.toolbox.client.ApiException e, int code, java.util.Map<String, java.util.List<String>> headers) { result.completeExceptionally(e); }
            public void onUploadProgress(long written, long length, boolean done) {}
            public void onDownloadProgress(long read, long length, boolean done) {}
        });
        return await(call, result);
    }
}
