// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.toolbox.client.api.ProcessApi;
import io.daytona.toolbox.client.model.ProcessKind;
import io.daytona.toolbox.client.model.ProcessLogChannel;
import io.daytona.toolbox.client.model.ProcessLogFrame;
import io.daytona.toolbox.client.model.ProcessLogPage;
import io.daytona.toolbox.client.model.ProcessResult;
import io.daytona.toolbox.client.model.ProcessState;
import io.daytona.toolbox.client.model.ProcessTerminalReason;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;

import java.util.Base64;
import java.util.ArrayList;
import java.util.List;

import io.daytona.sdk.exception.DaytonaException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ProcessSurfaceTest {
    @Mock
    private ProcessApi processApi;

    private Process process;

    @BeforeEach
    void setUp() {
        Sandbox sandbox = TestSupport.mockSandbox("http://127.0.0.1:1/toolbox");
        org.mockito.Mockito.lenient().when(sandbox.getId()).thenReturn("sandbox-1");
        process = new Process(processApi, sandbox);
    }

    @Test
    void runCollectsPaginatedBase64LogsWithoutCorruptingSplitUtf8() {
        io.daytona.toolbox.client.model.Process record = runningRecord("process-1");
        when(processApi.createProcess(org.mockito.ArgumentMatchers.any())).thenReturn(record);
        when(processApi.waitForProcess("process-1", 5000)).thenReturn(new ProcessResult()
                .exitCode(0)
                .reason(ProcessTerminalReason.ReasonExited));
        when(processApi.readProcessLogs(eq("process-1"), eq("start"), eq(1000), eq("base64"), isNull()))
                .thenReturn(new ProcessLogPage()
                        .frames(java.util.Collections.singletonList(frame(1, "cursor-1", new byte[] {
                                (byte) 0xf0, (byte) 0x9f
                        })))
                        .nextCursor("cursor-1")
                        .eof(false));
        when(processApi.readProcessLogs(eq("process-1"), eq("cursor-1"), eq(1000), eq("base64"), isNull()))
                .thenReturn(new ProcessLogPage()
                        .frames(java.util.Collections.singletonList(frame(2, "cursor-2", new byte[] {
                                (byte) 0x98, (byte) 0x80, ' ', 'o', 'k', '\n'
                        })))
                        .nextCursor("cursor-2")
                        .eof(true));

        ProcessRunOptions options = new ProcessRunOptions();
        options.setShellCommand("printf emoji").setWaitTimeoutMs(5000);
        ProcessRunResult result = process.run(options);

        assertThat(result.getStdout()).isEqualTo("😀 ok\n");
        assertThat(result.getStderr()).isEmpty();
        assertThat(result.getExitCode()).isZero();
        assertThat(result.isTimedOut()).isFalse();
        verify(processApi).readProcessLogs("process-1", "cursor-1", 1000, "base64", null);
    }

    @Test
    void processResultsDoNotExposeSerializationMethods() {
        assertThat(ProcessHandle.class.getDeclaredMethods())
                .extracting(java.lang.reflect.Method::getName)
                .doesNotContain("toJson", "fromJson");
        assertThat(ProcessRunResult.class.getDeclaredMethods())
                .extracting(java.lang.reflect.Method::getName)
                .doesNotContain("toJson");
    }

    @Test
    void streamLogsParsesBareFrameAndEofDataEvents() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(new MockResponse()
                    .setHeader("Content-Type", "text/event-stream")
                    .setBody("data: {\"seq\":1,\"cursor\":\"c1\",\"channel\":\"stdout\","
                            + "\"timestamp\":\"2026-08-13T00:00:00Z\",\"data\":\"b2s=\","
                            + "\"encoding\":\"base64\"}\n\n"
                            + "data: {\"cursor\":\"c1\"}\n\n"));
            Process streamingProcess = new Process(processApi,
                    TestSupport.mockSandbox(server.url("/toolbox/sandbox-1").toString()));
            ProcessHandle handle = new ProcessHandle("process-1", streamingProcess);
            List<ProcessLogEvent> events = new ArrayList<ProcessLogEvent>();

            handle.streamLogs("start", events::add);

            assertThat(events).extracting(ProcessLogEvent::getType).containsExactly("log", "eof");
            assertThat(events.get(0).getFrame().getData()).isEqualTo("b2s=");
            assertThat(server.takeRequest().getPath())
                    .isEqualTo("/toolbox/sandbox-1/processes/process-1/logs?follow=true&cursor=start&encoding=base64");
        }
    }

    @Test
    void runWithZeroWaitTimeoutAndCallbackTimesOutImmediately() {
        when(processApi.createProcess(org.mockito.ArgumentMatchers.any())).thenReturn(runningRecord("process-1"));
        when(processApi.waitForProcess("process-1", 1)).thenReturn(new ProcessResult()
                .reason(ProcessTerminalReason.ReasonTimedOut));
        List<String> chunks = new ArrayList<String>();

        ProcessRunResult result = process.run(new ProcessRunOptions()
                .setWaitTimeoutMs(0)
                .setOnStdout(chunks::add)
                .setArgv(java.util.Collections.singletonList("sleep")));

        assertThat(result.isTimedOut()).isTrue();
        assertThat(chunks).isEmpty();
        assertThat(result.getStdout()).isEmpty();
    }

    @Test
    void collectLogsThrowsWhenPageCapIsExhausted() {
        ProcessHandle handle = new ProcessHandle("process-1", process);
        java.util.concurrent.atomic.AtomicInteger pages = new java.util.concurrent.atomic.AtomicInteger();
        when(processApi.readProcessLogs(eq("process-1"), org.mockito.ArgumentMatchers.anyString(),
                eq(1000), eq("base64"), isNull()))
                .thenAnswer(invocation -> {
                    String next = "cursor-" + pages.incrementAndGet();
                    return new ProcessLogPage()
                            .frames(java.util.Collections.singletonList(
                                    frame(pages.get(), next, new byte[] { 'x' })))
                            .nextCursor(next)
                            .eof(false);
                });

        assertThatThrownBy(() -> process.collectLogs(handle, null, null))
                .isInstanceOf(DaytonaException.class)
                .hasMessageContaining("exceeded 10000 pages");
        assertThat(pages.get()).isEqualTo(10_000);
    }

    private static io.daytona.toolbox.client.model.Process runningRecord(String id) {
        return new io.daytona.toolbox.client.model.Process()
                .id(id)
                .createdAt("2026-08-13T00:00:00Z")
                .kind(ProcessKind.KindExec)
                .state(ProcessState.StateRunning);
    }

    private static ProcessLogFrame frame(int seq, String cursor, byte[] data) {
        return new ProcessLogFrame()
                .channel(ProcessLogChannel.ChannelStdout)
                .cursor(cursor)
                .seq(seq)
                .timestamp("2026-08-13T00:00:00Z")
                .encoding("base64")
                .data(Base64.getEncoder().encodeToString(data));
    }
}
