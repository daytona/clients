// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.daytona.api.client.ApiClient;
import io.daytona.api.client.api.SandboxApi;
import io.daytona.api.client.model.CreateSandboxSnapshot;
import io.daytona.api.client.model.ForkSandbox;
import io.daytona.api.client.model.GpuType;
import io.daytona.api.client.model.SandboxClass;
import io.daytona.api.client.model.SandboxDesiredState;
import io.daytona.api.client.model.SandboxLabels;
import io.daytona.api.client.model.SandboxState;
import io.daytona.api.client.model.ToolboxProxyUrl;
import io.daytona.api.client.model.UpdateSandboxSecrets;
import io.daytona.sdk.exception.DaytonaBadRequestException;
import io.daytona.sdk.exception.DaytonaConflictException;
import io.daytona.sdk.exception.DaytonaException;
import io.daytona.sdk.exception.DaytonaForbiddenException;
import io.daytona.sdk.exception.DaytonaNotFoundException;
import io.daytona.sdk.exception.DaytonaQueueTimeoutException;
import io.daytona.sdk.exception.DaytonaRateLimitException;
import io.daytona.sdk.exception.DaytonaServerException;
import io.daytona.sdk.exception.DaytonaSpotEvictedException;
import io.daytona.sdk.exception.DaytonaTimeoutException;
import io.daytona.sdk.exception.DaytonaUnprocessableEntityException;
import io.daytona.sdk.internal.EventDispatcher;
import io.daytona.sdk.internal.EventSubscriptionManager;
import io.daytona.sdk.internal.FakeEventGateway;
import io.daytona.toolbox.client.api.InfoApi;
import io.daytona.toolbox.client.api.ServerApi;
import io.daytona.toolbox.client.model.UpdateEnvRequest;
import io.daytona.toolbox.client.model.UserHomeDirResponse;
import io.daytona.toolbox.client.model.WorkDirResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.lang.reflect.Method;
import java.time.Duration;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BiConsumer;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.entry;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SandboxTest {

    @Mock
    private SandboxApi sandboxApi;

    @Mock
    private InfoApi infoApi;

    @Mock
    private ServerApi serverApi;

    private Sandbox sandbox;

    @BeforeEach
    void setUp() {
        sandbox = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-1", SandboxState.STARTED), () -> null, mockSubscriptionManager());
    }

    @Test
    void constructorPopulatesFieldsAndChildServices() {
        assertThat(sandbox.getId()).isEqualTo("sb-1");
        assertThat(sandbox.getName()).isEqualTo("sandbox-sb-1");
        assertThat(sandbox.getState()).isEqualTo("started");
        assertThat(sandbox.getProcess()).isNotNull();
        assertThat(sandbox.getFs()).isNotNull();
        assertThat(sandbox.getGit()).isNotNull();
        assertThat(sandbox.computerUse).isNotNull();
        assertThat(sandbox.codeInterpreter).isNotNull();
        assertThat(sandbox.createLspServer("python", "/project")).isNotNull();
        assertThat(sandbox.getLanguage()).isEqualTo("python");
    }

    @Test
    void constructorLoadsToolboxProxyUrlWhenMissing() {
        io.daytona.api.client.model.Sandbox model = TestSupport.mainSandbox("sb-2", SandboxState.STARTED);
        model.setToolboxProxyUrl("");
        when(sandboxApi.getToolboxProxyUrl("sb-2", null)).thenReturn(new ToolboxProxyUrl().url("https://proxy.example"));

        Sandbox loaded = new Sandbox(sandboxApi, TestSupport.config(), model, () -> null, mockSubscriptionManager());

        assertThat(loaded.getToolboxProxyUrl()).isEqualTo("https://proxy.example");
        assertThat(loaded.getToolboxApiClient().getBasePath()).isEqualTo("https://proxy.example/sb-2");
    }

    @Test
    void constructorUsesLanguageFromLabelsAndFallsBackToEmptyStrings() {
        io.daytona.api.client.model.Sandbox model = new io.daytona.api.client.model.Sandbox();
        model.setId("sb-3");
        model.setState(SandboxState.STARTED);
        model.setLabels(Collections.singletonMap(Daytona.CODE_TOOLBOX_LANGUAGE_LABEL, "javascript"));
        model.setToolboxProxyUrl("https://proxy.example/");

        Sandbox loaded = new Sandbox(sandboxApi, TestSupport.config(), model, () -> null, mockSubscriptionManager());

        assertThat(loaded.getLanguage()).isEqualTo("javascript");
        assertThat(loaded.getName()).isEmpty();
        assertThat(loaded.getEnv()).isEmpty();
        assertThat(loaded.getToolboxApiClient().getBasePath()).isEqualTo("https://proxy.example/sb-3");
    }

    @Test
    void startUpdatesStateAndWaitsUntilStarted() {
        when(sandboxApi.startSandbox("sb-1", null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTING));
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        sandbox.start(2);

        assertThat(sandbox.getState()).isEqualTo("started");
    }

    @Test
    void waitUntilStartedRejectsInvalidTimeoutAndFailureState() {
        assertThatThrownBy(() -> sandbox.waitUntilStarted(-1))
                .hasMessageContaining("Timeout must be non-negative");

        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.ERROR));
        TestSupport.setField(sandbox, "state", "starting");

        assertThatThrownBy(() -> sandbox.waitUntilStarted(2))
                .hasMessageContaining("Sandbox sb-1 entered error state: error");
    }

    @Test
    void waitUntilStartedTimesOutWhenStateNeverChanges() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTING));
        TestSupport.setField(sandbox, "state", "starting");

        assertThatThrownBy(() -> sandbox.waitUntilStarted(1))
                .hasMessageContaining("did not reach target state within 1 seconds");
    }

    @Test
    void waitUntilStartedThrowsQueueTimeoutExceptionWhenDestroyedByQueueTimeout() {
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setQueueTimeout(15);
        destroyed.setQueueTimedOutAt("2026-09-29T12:00:00Z");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(destroyed);
        TestSupport.setField(sandbox, "state", "pending_build");

        assertThatThrownBy(() -> sandbox.waitUntilStarted(2))
                .isInstanceOf(DaytonaQueueTimeoutException.class)
                .isInstanceOf(DaytonaTimeoutException.class)
                .hasMessage("Sandbox sb-1 was destroyed after waiting 15 minutes for a runner (queue timed out at 2026-09-29T12:00:00Z)");
    }

    @Test
    void waitUntilStartedThrowsSpotEvictedExceptionWhenDestroyedByPreemption() {
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setSpotEvictedAt("2026-09-29T12:00:00Z");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(destroyed);
        TestSupport.setField(sandbox, "state", "starting");

        assertThatThrownBy(() -> sandbox.waitUntilStarted(2))
                .isInstanceOf(DaytonaSpotEvictedException.class)
                .isNotInstanceOf(DaytonaTimeoutException.class)
                .hasMessage("Sandbox sb-1 was evicted by spot preemption at 2026-09-29T12:00:00Z");
    }

    @Test
    void waitUntilStartedQueueTimeoutMessageOmitsDurationWhenUnknown() {
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setQueueTimedOutAt("2026-09-29T12:00:00Z");
        Sandbox cached = new Sandbox(sandboxApi, TestSupport.config(), destroyed, () -> null, mockSubscriptionManager());

        assertThatThrownBy(() -> cached.waitUntilStarted(2))
                .isInstanceOf(DaytonaQueueTimeoutException.class)
                .hasMessage("Sandbox sb-1 was destroyed after waiting for a runner (queue timed out at 2026-09-29T12:00:00Z)");
    }

    @Test
    void waitUntilStartedFailsFastOnCachedQueueTimedOutState() {
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setQueueTimeout(1);
        destroyed.setQueueTimedOutAt("2026-09-29T12:00:00Z");
        Sandbox cached = new Sandbox(sandboxApi, TestSupport.config(), destroyed, () -> null, mockSubscriptionManager());

        assertThatThrownBy(() -> cached.waitUntilStarted(2))
                .isInstanceOf(DaytonaQueueTimeoutException.class)
                .hasMessage("Sandbox sb-1 was destroyed after waiting 1 minute for a runner (queue timed out at 2026-09-29T12:00:00Z)");
        verify(sandboxApi, org.mockito.Mockito.never()).getSandbox("sb-1", null, null);
    }

    @Test
    void waitUntilStartedFailsFastOnCachedSpotEvictedState() {
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setSpotEvictedAt("2026-09-29T12:00:00Z");
        Sandbox cached = new Sandbox(sandboxApi, TestSupport.config(), destroyed, () -> null, mockSubscriptionManager());

        assertThatThrownBy(() -> cached.waitUntilStarted(2))
                .isInstanceOf(DaytonaSpotEvictedException.class);
        verify(sandboxApi, org.mockito.Mockito.never()).getSandbox("sb-1", null, null);
    }

    @Test
    void waitUntilStartedFallsBackToPollingWhenDeprecatedPollingIsEnabled() {
        Sandbox pollingSandbox = new Sandbox(
                sandboxApi,
                TestSupport.config(),
                TestSupport.mainSandbox("sb-poll", SandboxState.STARTING),
                () -> null,
                new EventSubscriptionManager(null));
        when(sandboxApi.getSandbox("sb-poll", null, null)).thenReturn(TestSupport.mainSandbox("sb-poll", SandboxState.STARTED));

        pollingSandbox.waitUntilStarted(2);

        assertThat(pollingSandbox.getState()).isEqualTo("started");
    }

    @Test
    void stopWithAnEmptyResponseRefreshesOnceAndWaitsUntilStopped() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STOPPED));

        sandbox.stop(2);

        assertThat(sandbox.getState()).isEqualTo("stopped");
        verify(sandboxApi).stopSandbox("sb-1", null, null);
        verify(sandboxApi, times(1)).getSandbox("sb-1", null, null);
    }

    @Test
    void stopAppliesTheReturnedSandboxWithoutAGet() {
        when(sandboxApi.stopSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STOPPED));

        sandbox.stop(2);

        assertThat(sandbox.getState()).isEqualTo("stopped");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
    }

    @Test
    void waitUntilStoppedRejectsFailureAndInvalidTimeout() {
        assertThatThrownBy(() -> sandbox.waitUntilStopped(-1))
                .hasMessageContaining("Timeout must be non-negative");

        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.ERROR));
        TestSupport.setField(sandbox, "state", "stopping");

        assertThatThrownBy(() -> sandbox.waitUntilStopped(2))
                .hasMessageContaining("Sandbox sb-1 entered error state: error");
    }

    @Test
    void waitUntilStoppedAllowsDestroyedState() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED));
        TestSupport.setField(sandbox, "state", "stopping");

        sandbox.waitUntilStopped(2);

        assertThat(sandbox.getState()).isEqualTo("destroyed");
    }

    @Test
    void deleteDefaultDoesNotWaitForDestroyed() {
        sandbox.delete(5);

        verify(sandboxApi).deleteSandbox("sb-1", null);
        verify(sandboxApi, org.mockito.Mockito.never()).getSandbox("sb-1", null, null);
    }

    @Test
    void deleteWithWaitBlocksUntilDestroyed() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(
                TestSupport.mainSandbox("sb-1", SandboxState.DESTROYING),
                TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED)
        );

        sandbox.delete(5, true);

        verify(sandboxApi).deleteSandbox("sb-1", null);
        assertThat(sandbox.getState()).isEqualTo("destroyed");
    }

    @Test
    void deleteWithWaitAllowsDestroyedTargetEvenWhenQueueTimeoutMarkerExists() {
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setQueueTimeout(15);
        destroyed.setQueueTimedOutAt("2026-09-29T12:00:00Z");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(
                TestSupport.mainSandbox("sb-1", SandboxState.DESTROYING),
                destroyed
        );

        sandbox.delete(5, true);

        verify(sandboxApi).deleteSandbox("sb-1", null);
        assertThat(sandbox.getState()).isEqualTo("destroyed");
    }

    @Test
    void refreshDataIgnoresNullApiPayload() {
        TestSupport.setField(sandbox, "name", "before");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(null);

        sandbox.refreshData();

        assertThat(sandbox.getName()).isEqualTo("before");
    }

    @Test
    void setLabelsAndIntervalsUpdateLocalState() throws Exception {
        when(sandboxApi.replaceLabels(eq("sb-1"), any(), isNull()))
                .thenReturn(new SandboxLabels().labels(Collections.singletonMap("team", "sdk")));

        io.daytona.api.client.model.Sandbox refreshed = TestSupport.mainSandbox("sb-1", SandboxState.STARTED);
        refreshed.setLabels(Collections.singletonMap("team", "sdk"));
        refreshed.setAutoStopInterval(BigDecimal.ONE);
        refreshed.setAutoPauseInterval(BigDecimal.valueOf(4));
        refreshed.setAutoArchiveInterval(BigDecimal.valueOf(2));
        refreshed.setAutoDeleteInterval(BigDecimal.valueOf(3));
        when(sandboxApi.setAutostopInterval("sb-1", BigDecimal.valueOf(1), null)).thenReturn(refreshed);
        when(sandboxApi.setAutoPauseInterval("sb-1", BigDecimal.valueOf(4), null)).thenReturn(refreshed);
        when(sandboxApi.setAutoArchiveInterval("sb-1", BigDecimal.valueOf(2), null)).thenReturn(refreshed);
        when(sandboxApi.setAutoDeleteInterval("sb-1", BigDecimal.valueOf(3), null)).thenReturn(refreshed);

        Map<String, String> labels = sandbox.setLabels(Collections.singletonMap("team", "sdk"));
        sandbox.setAutostopInterval(1);
        sandbox.setAutoPauseInterval(4);
        sandbox.setAutoArchiveInterval(2);
        sandbox.setAutoDeleteInterval(3);

        assertThat(labels).containsEntry("team", "sdk");
        assertThat(sandbox.getAutoStopInterval()).isEqualTo(1);
        assertThat(sandbox.getAutoPauseInterval()).isEqualTo(4);
        assertThat(sandbox.getAutoArchiveInterval()).isEqualTo(2);
        assertThat(sandbox.getAutoDeleteInterval()).isEqualTo(3);
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
    }

    @Test
    void setLabelsWithAnEmptyResponseRefreshesOnce() {
        io.daytona.api.client.model.Sandbox refreshed = TestSupport.mainSandbox("sb-1", SandboxState.STARTED);
        refreshed.setLabels(Collections.singletonMap("team", "sdk"));
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(refreshed);

        assertThat(sandbox.setLabels(Collections.singletonMap("team", "sdk"))).containsEntry("team", "sdk");

        verify(sandboxApi).replaceLabels(eq("sb-1"), any(), isNull());
        verify(sandboxApi, times(1)).getSandbox("sb-1", null, null);
    }

    @SuppressWarnings("deprecation")
    @Test
    void replaceLabelsResponseDeserializesIntoTheLabelsModel() throws Exception {
        try (okhttp3.mockwebserver.MockWebServer server = new okhttp3.mockwebserver.MockWebServer()) {
            server.enqueue(new okhttp3.mockwebserver.MockResponse()
                    .setHeader("Content-Type", "application/json")
                    .setBody("{\"labels\":{\"team\":\"sdk\"}}"));
            server.enqueue(new okhttp3.mockwebserver.MockResponse().setResponseCode(200));
            // The SDK's own API client setup, so the generated client deserializes as it does in use.
            try (Daytona daytona = new Daytona(new DaytonaConfig.Builder()
                    .apiKey("test-key")
                    .apiUrl(server.url("/api").toString())
                    .useDeprecatedPolling(true)
                    .build())) {
                SandboxApi api = TestSupport.getField(daytona, "sandboxApi", SandboxApi.class);

                SandboxLabels labels = api.replaceLabels("sb-1", new SandboxLabels().labels(Map.of("team", "sdk")), null);
                SandboxLabels empty = api.replaceLabels("sb-1", new SandboxLabels().labels(Map.of("team", "sdk")), null);

                assertThat(labels.getLabels()).containsExactly(entry("team", "sdk"));
                assertThat(empty).isNull();
            }
        }
    }

    @Test
    void setPublicUpdatesPublicStatus() throws Exception {
        io.daytona.api.client.model.Sandbox updated = TestSupport.mainSandbox("sb-1", SandboxState.STARTED);
        updated.setPublic(true);
        when(sandboxApi.updatePublicStatus("sb-1", true, null)).thenReturn(updated);

        sandbox.setPublic(true);

        org.mockito.Mockito.verify(sandboxApi).updatePublicStatus("sb-1", true, null);
        assertThat(sandbox.getPublic()).isTrue();
    }

    @Test
    void setTtlCallsApiAndRejectsNegative() {
        sandbox.setTtl(60);

        verify(sandboxApi).setTtl("sb-1", BigDecimal.valueOf(60), null);

        assertThatThrownBy(() -> sandbox.setTtl(-1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("ttlMinutes must be a non-negative integer");
    }

    @Test
    void autoDestroyAtIsPopulatedFromDTO() {
        io.daytona.api.client.model.Sandbox model = TestSupport.mainSandbox("sb-exp", SandboxState.STARTED);
        model.setAutoDestroyAt("2026-09-15T12:00:00Z");
        model.setQueueTimeout(25);
        model.setQueueTimedOutAt("2026-09-15T11:00:00Z");

        Sandbox loaded = new Sandbox(sandboxApi, TestSupport.config(), model, () -> null, mockSubscriptionManager());

        assertThat(loaded.getAutoDestroyAt()).isEqualTo("2026-09-15T12:00:00Z");
        assertThat(loaded.getQueueTimeout()).isEqualTo(25);
        assertThat(loaded.getQueueTimedOutAt()).isEqualTo("2026-09-15T11:00:00Z");
    }

    @Test
    void queueTimeoutIsNullableWhenHydratedFromDTO() {
        io.daytona.api.client.model.Sandbox model = TestSupport.mainSandbox("sb-null-queue", SandboxState.STARTED);
        model.setQueueTimeout(null);
        model.setQueueTimedOutAt(null);

        Sandbox loaded = new Sandbox(sandboxApi, TestSupport.config(), model, () -> null, mockSubscriptionManager());

        assertThat(loaded.getQueueTimeout()).isNull();
        assertThat(loaded.getQueueTimedOutAt()).isNull();
    }

    @Test
    void newFieldsArePopulatedFromFullDTO() {
        Sandbox loaded = new Sandbox(sandboxApi, TestSupport.config(),
                TestSupport.mainSandbox("sb-new", SandboxState.STARTED), () -> null, mockSubscriptionManager());

        assertThat(loaded.getSandboxClass()).isEqualTo(SandboxClass.LINUX_VM);
        assertThat(loaded.getWarmPoolId()).isEqualTo("wp-1");
        assertThat(loaded.getGpuType()).isEqualTo(GpuType.H100);
        assertThat(loaded.getDesiredState()).isEqualTo(SandboxDesiredState.STARTED);
        assertThat(loaded.getDaemonVersion()).isEqualTo("1.2.3");
        assertThat(loaded.getOtelEndpointOverride()).isEqualTo("http://otel.example:4318");
    }

    @Test
    void sharedFieldsArePopulatedFromSandboxListItem() {
        Sandbox loaded = new Sandbox(sandboxApi, TestSupport.config(),
                TestSupport.mainSandboxListItem("sb-li", SandboxState.STARTED), () -> null, mockSubscriptionManager());

        assertThat(loaded.getSandboxClass()).isEqualTo(SandboxClass.LINUX_VM);
        assertThat(loaded.getWarmPoolId()).isEqualTo("wp-1");
        assertThat(loaded.getGpuType()).isEqualTo(GpuType.H100);
        assertThat(loaded.getDesiredState()).isEqualTo(SandboxDesiredState.STARTED);
        assertThat(loaded.getDaemonVersion()).isEqualTo("1.2.3");
        assertThat(loaded.getOtelEndpointOverride()).isNull();
    }

    @Test
    void updateSecretsSendsSingletonMapListAndUpdatesLocalState() {
        io.daytona.api.client.model.Sandbox refreshed = TestSupport.mainSandbox("sb-1", SandboxState.STARTED);
        refreshed.setLabels(Collections.singletonMap("team", "sdk"));
        when(sandboxApi.updateSandboxSecrets(eq("sb-1"), any(UpdateSandboxSecrets.class), isNull())).thenReturn(refreshed);

        sandbox.updateSecrets(Collections.singletonMap("FOO", "foo-secret"));

        ArgumentCaptor<UpdateSandboxSecrets> captor = ArgumentCaptor.forClass(UpdateSandboxSecrets.class);
        verify(sandboxApi).updateSandboxSecrets(eq("sb-1"), captor.capture(), isNull());
        assertThat(captor.getValue().getSecrets()).containsExactly(Collections.singletonMap("FOO", "foo-secret"));
        assertThat(sandbox.getLabels()).containsEntry("team", "sdk");
    }

    @Test
    void updateSecretsWithEmptyMapSendsEmptyListAndIgnoresNullResponse() {
        when(sandboxApi.updateSandboxSecrets(eq("sb-1"), any(UpdateSandboxSecrets.class), isNull())).thenReturn(null);

        sandbox.updateSecrets(Collections.<String, String>emptyMap());

        ArgumentCaptor<UpdateSandboxSecrets> captor = ArgumentCaptor.forClass(UpdateSandboxSecrets.class);
        verify(sandboxApi).updateSandboxSecrets(eq("sb-1"), captor.capture(), isNull());
        assertThat(captor.getValue().getSecrets()).isEmpty();
        assertThat(sandbox.getName()).isEqualTo("sandbox-sb-1");
    }

    @Test
    void updateEnvSendsSetAndUnset() {
        TestSupport.setField(sandbox, "serverApi", serverApi);
        // The daemon responds with a status message, which updateEnv discards.
        when(serverApi.updateEnv(any(UpdateEnvRequest.class)))
                .thenReturn(Collections.singletonMap("message", "Environment updated successfully"));

        sandbox.updateEnv(Collections.singletonMap("FOO", "bar"), Collections.singletonList("BAZ"));

        ArgumentCaptor<UpdateEnvRequest> captor = ArgumentCaptor.forClass(UpdateEnvRequest.class);
        verify(serverApi).updateEnv(captor.capture());
        assertThat(captor.getValue().getSet()).containsExactly(entry("FOO", "bar"));
        assertThat(captor.getValue().getUnset()).containsExactly("BAZ");
        assertThat(captor.getValue().getUnsetValuePrefix()).isNull();
    }

    @Test
    void updateEnvSingleArgLeavesUnsetEmpty() {
        TestSupport.setField(sandbox, "serverApi", serverApi);
        when(serverApi.updateEnv(any(UpdateEnvRequest.class))).thenReturn(null);

        sandbox.updateEnv(Collections.singletonMap("FOO", "bar"));

        ArgumentCaptor<UpdateEnvRequest> captor = ArgumentCaptor.forClass(UpdateEnvRequest.class);
        verify(serverApi).updateEnv(captor.capture());
        assertThat(captor.getValue().getSet()).containsExactly(entry("FOO", "bar"));
        assertThat(captor.getValue().getUnset()).isEmpty();
        assertThat(captor.getValue().getUnsetValuePrefix()).isNull();
    }

    @Test
    void userDirectoryMethodsUseInfoApi() {
        TestSupport.setField(sandbox, "infoApi", infoApi);
        when(infoApi.getUserHomeDir()).thenReturn(new UserHomeDirResponse().dir("/home/daytona"));
        when(infoApi.getWorkDir()).thenReturn(new WorkDirResponse().dir("/workspace"));

        assertThat(sandbox.getUserHomeDir()).isEqualTo("/home/daytona");
        assertThat(sandbox.getWorkDir()).isEqualTo("/workspace");
    }

    @Test
    void userDirectoryMethodsReturnEmptyStringForNullResponses() {
        TestSupport.setField(sandbox, "infoApi", infoApi);
        when(infoApi.getUserHomeDir()).thenReturn(null);
        when(infoApi.getWorkDir()).thenReturn(null);

        assertThat(sandbox.getUserHomeDir()).isEmpty();
        assertThat(sandbox.getWorkDir()).isEmpty();
    }

    @Test
    void forkAndSnapshotDelegate() {
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull())).thenReturn(TestSupport.mainSandbox("sb-2", SandboxState.STARTED));
        io.daytona.api.client.model.Sandbox snapshotting = TestSupport.mainSandbox("sb-1", SandboxState.SNAPSHOTTING);
        io.daytona.api.client.model.Sandbox started = TestSupport.mainSandbox("sb-1", SandboxState.STARTED);
        when(sandboxApi.createSandboxSnapshot(eq("sb-1"), any(CreateSandboxSnapshot.class), isNull())).thenReturn(snapshotting);
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(started);

        Sandbox forked = sandbox.fork("forked", 2);
        sandbox.createSnapshot("snap-1", 2);

        assertThat(forked.getId()).isEqualTo("sb-2");
        verify(sandboxApi).createSandboxSnapshot(eq("sb-1"), any(CreateSandboxSnapshot.class), isNull());
    }

    @Test
    void forkRejectsNegativeTimeoutBeforeApiCall() {
        assertThatThrownBy(() -> sandbox.fork("forked", -1))
                .hasMessageContaining("Timeout must be non-negative");

        verify(sandboxApi, org.mockito.Mockito.never()).forkSandbox(any(), any(), any());
    }

    @Test
    void forkDefaultArgumentsOmitName() {
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull())).thenReturn(TestSupport.mainSandbox("sb-4", SandboxState.STARTED));

        sandbox.fork();

        ArgumentCaptor<ForkSandbox> captor = ArgumentCaptor.forClass(ForkSandbox.class);
        verify(sandboxApi).forkSandbox(eq("sb-1"), captor.capture(), isNull());
        assertThat(captor.getValue().getName()).isNull();
    }

    @Test
    void createSnapshotFailsForErrorState() {
        io.daytona.api.client.model.Sandbox snapshotting = TestSupport.mainSandbox("sb-1", SandboxState.SNAPSHOTTING);
        io.daytona.api.client.model.Sandbox error = TestSupport.mainSandbox("sb-1", SandboxState.ERROR);
        when(sandboxApi.createSandboxSnapshot(eq("sb-1"), any(CreateSandboxSnapshot.class), isNull())).thenReturn(snapshotting);
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(error);

        assertThatThrownBy(() -> sandbox.createSnapshot("snap-err", 2))
                .hasMessageContaining("Sandbox sb-1 entered error state: error");
    }

    @Test
    void createSnapshotTimesOutWhenSnapshottingPersists() {
        io.daytona.api.client.model.Sandbox snapshotting = TestSupport.mainSandbox("sb-1", SandboxState.SNAPSHOTTING);
        when(sandboxApi.createSandboxSnapshot(eq("sb-1"), any(CreateSandboxSnapshot.class), isNull())).thenReturn(snapshotting);
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(snapshotting, snapshotting);

        assertThatThrownBy(() -> sandbox.createSnapshot("snap-timeout", 2))
                .hasMessageContaining("did not reach target state within 2 seconds");
    }

    @Test
    void deprecatedExperimentalForkDelegatesToFork() {
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull())).thenReturn(TestSupport.mainSandbox("sb-3", SandboxState.STARTED));

        @SuppressWarnings("deprecation")
        Sandbox forked = sandbox.experimentalFork("dep-fork", 2);

        assertThat(forked.getId()).isEqualTo("sb-3");
        verify(sandboxApi).forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull());
    }

    @Test
    void deprecatedExperimentalForkNoArgDelegatesToFork() {
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull())).thenReturn(TestSupport.mainSandbox("sb-5", SandboxState.STARTED));

        @SuppressWarnings("deprecation")
        Sandbox forked = sandbox.experimentalFork();

        assertThat(forked.getId()).isEqualTo("sb-5");
        ArgumentCaptor<ForkSandbox> captor = ArgumentCaptor.forClass(ForkSandbox.class);
        verify(sandboxApi).forkSandbox(eq("sb-1"), captor.capture(), isNull());
        assertThat(captor.getValue().getName()).isNull();
    }

    @Test
    void deprecatedExperimentalCreateSnapshotDelegatesToCreateSnapshot() {
        io.daytona.api.client.model.Sandbox snapshotting = TestSupport.mainSandbox("sb-1", SandboxState.SNAPSHOTTING);
        io.daytona.api.client.model.Sandbox started = TestSupport.mainSandbox("sb-1", SandboxState.STARTED);
        when(sandboxApi.createSandboxSnapshot(eq("sb-1"), any(CreateSandboxSnapshot.class), isNull())).thenReturn(snapshotting);
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(started);

        @SuppressWarnings("deprecation")
        Object unused = callDeprecatedCreateSnapshot("snap-dep", 2);

        verify(sandboxApi).createSandboxSnapshot(eq("sb-1"), any(CreateSandboxSnapshot.class), isNull());
    }

    /** Helper to call deprecated void method in an expression context for @SuppressWarnings. */
    @SuppressWarnings("deprecation")
    private Object callDeprecatedCreateSnapshot(String name, long timeout) {
        sandbox.experimentalCreateSnapshot(name, timeout);
        return null;
    }

    @ParameterizedTest
    @MethodSource("mappedMainApiExceptions")
    void lifecycleMethodsMapApiErrors(int status, Class<? extends RuntimeException> type) {
        when(sandboxApi.startSandbox("sb-1", null))
                .thenThrow(new io.daytona.api.client.ApiException(status, "boom", null, "{\"message\":\"mapped\"}"));

        assertThatThrownBy(() -> sandbox.start(1))
                .isInstanceOf(type)
                .hasMessage("mapped");
    }

    private static Stream<Arguments> mappedMainApiExceptions() {
        return Stream.of(
                Arguments.of(400, DaytonaBadRequestException.class),
                Arguments.of(403, DaytonaForbiddenException.class),
                Arguments.of(404, DaytonaNotFoundException.class),
                Arguments.of(409, DaytonaConflictException.class),
                Arguments.of(422, DaytonaUnprocessableEntityException.class),
                Arguments.of(429, DaytonaRateLimitException.class),
                Arguments.of(500, DaytonaServerException.class)
        );
    }

    @Test
    void cachedErrorStateRecoverAfterRefresh() {
        TestSupport.setField(sandbox, "state", "error");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        sandbox.waitUntilStarted(2);

        assertThat(sandbox.getState()).isEqualTo("started");
    }

    @Test
    void nearZeroTimeoutStillPerformsOneRefresh() {
        TestSupport.setField(sandbox, "state", "starting");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        sandbox.waitUntilStarted(1);

        assertThat(sandbox.getState()).isEqualTo("started");
    }

    @Test
    void pauseAppliesTheReturnedSandboxWithoutAGet() {
        when(sandboxApi.pauseSandbox("sb-1", null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.PAUSED));

        sandbox.pause(2);

        assertThat(sandbox.getState()).isEqualTo("paused");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
    }

    @Test
    void pauseWithAnEmptyResponseRefreshesOnce() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.PAUSED));

        sandbox.pause(2);

        assertThat(sandbox.getState()).isEqualTo("paused");
        verify(sandboxApi, times(1)).getSandbox("sb-1", null, null);
    }

    @Test
    void pauseLandingInStoppedResolves() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STOPPED));

        sandbox.pause(2);

        verify(sandboxApi).pauseSandbox("sb-1", null);
        assertThat(sandbox.getState()).isEqualTo("stopped");
    }

    @Test
    void pauseLandingInArchivedResolves() {
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.ARCHIVED));

        sandbox.pause(2);

        verify(sandboxApi).pauseSandbox("sb-1", null);
        assertThat(sandbox.getState()).isEqualTo("archived");
    }

    @Test
    void concurrentWaitersAllComplete() throws Exception {
        int waiterCount = 5;
        Thread[] threads = new Thread[waiterCount];
        Throwable[] errors = new Throwable[waiterCount];

        for (int i = 0; i < waiterCount; i++) {
            final int idx = i;
            String sbId = "sb-concurrent-" + i;
            SandboxApi localApi = mock(SandboxApi.class);
            when(localApi.getSandbox(sbId, null, null)).thenAnswer(inv -> {
                Thread.sleep(50);
                return TestSupport.mainSandbox(sbId, SandboxState.STARTED);
            });

            Sandbox sb = new Sandbox(localApi, TestSupport.config(),
                    TestSupport.mainSandbox(sbId, SandboxState.STARTING), () -> null, mockSubscriptionManager());

            threads[i] = new Thread(() -> {
                try {
                    sb.waitUntilStarted(5);
                } catch (Throwable e) {
                    errors[idx] = e;
                }
            });
        }

        for (Thread t : threads) t.start();
        for (Thread t : threads) t.join(10_000);

        for (int i = 0; i < waiterCount; i++) {
            assertThat(errors[i]).as("waiter-%d should complete without error", i).isNull();
        }
    }

    @Test
    void persistentErrorStateRejectsWait() {
        TestSupport.setField(sandbox, "state", "error");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.ERROR));

        long start = System.nanoTime();
        assertThatThrownBy(() -> sandbox.waitUntilStarted(5))
                .isInstanceOf(DaytonaException.class)
                .isNotInstanceOf(DaytonaTimeoutException.class)
                .hasMessageContaining("error");
        long elapsedSeconds = Duration.ofNanos(System.nanoTime() - start).getSeconds();
        assertThat(elapsedSeconds).isLessThan(4);
    }

    @Test
    void persistentErrorStateRejectsWaitWithoutTimeout() {
        TestSupport.setField(sandbox, "state", "error");
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.ERROR));

        assertTimeoutPreemptively(Duration.ofSeconds(10), () ->
                assertThatThrownBy(() -> sandbox.waitUntilStarted(0))
                        .isInstanceOf(DaytonaException.class)
                        .hasMessageContaining("error"));
    }

    @Test
    void constructorsListItemsAndMethodCallsDoNotSubscribe() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox fromModel = new Sandbox(sandboxApi, TestSupport.config(),
                TestSupport.mainSandbox("sb-1", SandboxState.STARTED), () -> null, manager);
        new Sandbox(sandboxApi, TestSupport.config(),
                TestSupport.mainSandboxListItem("sb-2", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.getSandbox("sb-1", null, null)).thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));
        when(sandboxApi.setAutostopInterval("sb-1", BigDecimal.valueOf(5), null))
                .thenReturn(TestSupport.mainSandbox("sb-1", SandboxState.STARTED));

        fromModel.refreshData();
        fromModel.setAutostopInterval(5);
        fromModel.createLspServer("python", "/project");
        fromModel.waitUntilStarted(1);

        verify(manager, never()).subscribe(any(), any(), any());
        verify(manager, never()).refresh(any());
    }

    @Test
    void waitReturningOnCachedTargetStateDoesNotSubscribe() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-1", manager);
        java.lang.reflect.Method waitForState = Sandbox.class.getDeclaredMethod(
                "waitForState", java.util.Set.class, java.util.Set.class, long.class, boolean.class);
        waitForState.setAccessible(true);

        waitForState.invoke(sb, java.util.Set.of("starting"), java.util.Set.of("error"), 5L, false);

        verify(manager, never()).subscribe(any(), any(), any());
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
    }

    @Test
    void waitFailingFastOnCachedQueueTimeoutDoesNotSubscribe() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        io.daytona.api.client.model.Sandbox destroyed = TestSupport.mainSandbox("sb-1", SandboxState.DESTROYED);
        destroyed.setQueueTimedOutAt("2026-09-29T12:00:00Z");
        Sandbox cached = new Sandbox(sandboxApi, TestSupport.config(), destroyed, () -> null, manager);

        assertThatThrownBy(() -> cached.waitUntilStarted(2)).isInstanceOf(DaytonaQueueTimeoutException.class);

        verify(manager, never()).subscribe(any(), any(), any());
    }

    @Test
    void waitSubscribesToStateEventsOnlyAndUnsubscribesOnSuccess() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-w", manager);
        when(sandboxApi.getSandbox("sb-w", null, null)).thenReturn(TestSupport.mainSandbox("sb-w", SandboxState.STARTED));

        sb.waitUntilStarted(2);

        verify(manager).subscribe(eq("sb-w"), any(), eq(List.of("sandbox.state.updated")));
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void waitUnsubscribesOnErrorState() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-w", manager);
        when(sandboxApi.getSandbox("sb-w", null, null)).thenReturn(TestSupport.mainSandbox("sb-w", SandboxState.ERROR));

        assertThatThrownBy(() -> sb.waitUntilStarted(2)).isInstanceOf(DaytonaException.class);

        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void waitUnsubscribesOnTimeout() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-w", manager);
        when(sandboxApi.getSandbox("sb-w", null, null)).thenReturn(TestSupport.mainSandbox("sb-w", SandboxState.STARTING));

        assertThatThrownBy(() -> sb.waitUntilStarted(1)).isInstanceOf(DaytonaTimeoutException.class);

        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void waitUnsubscribesOnPollFailure() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-w", manager);
        when(sandboxApi.getSandbox("sb-w", null, null)).thenThrow(new IllegalStateException("boom"));

        assertThatThrownBy(() -> sb.waitUntilStarted(2)).isInstanceOf(RuntimeException.class);

        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void waitUnsubscribesWhenInterrupted() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-w", manager);
        lenient().when(sandboxApi.getSandbox("sb-w", null, null)).thenReturn(TestSupport.mainSandbox("sb-w", SandboxState.STARTING));
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread waiter = new Thread(() -> {
            try {
                sb.waitUntilStarted(0);
            } catch (Throwable e) {
                error.set(e);
            }
        });

        waiter.start();
        verify(manager, timeout(2000)).subscribe(eq("sb-w"), any(), any());
        waiter.interrupt();
        waiter.join(5000);

        assertThat(error.get()).isInstanceOf(DaytonaException.class).hasMessageContaining("Interrupted");
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void longWaitSubscribesAgainWhenTheSubscriptionExpired() {
        EventSubscriptionManager manager = mock(EventSubscriptionManager.class);
        when(manager.subscribe(eq("sb-w"), any(), any())).thenReturn("sub-1", "sub-2");
        lenient().when(manager.refresh("sub-1")).thenReturn(false);
        lenient().when(manager.refresh("sub-2")).thenReturn(true);
        Sandbox sb = startingSandbox("sb-w", manager);
        io.daytona.api.client.model.Sandbox starting = TestSupport.mainSandbox("sb-w", SandboxState.STARTING);
        when(sandboxApi.getSandbox("sb-w", null, null)).thenReturn(starting, starting, starting,
                TestSupport.mainSandbox("sb-w", SandboxState.STARTED));

        sb.waitUntilStarted(5);

        verify(manager, times(2)).subscribe(eq("sb-w"), any(), any());
        verify(manager).unsubscribe("sub-2");
        verify(manager, never()).unsubscribe("sub-1");
    }

    @Test
    void concurrentWaitsOnTheSameSandboxEachHoldAndReleaseTheirOwnSubscription() throws Exception {
        EventSubscriptionManager manager = mock(EventSubscriptionManager.class);
        when(manager.subscribe(eq("sb-w"), any(), any())).thenReturn("sub-a", "sub-b");
        lenient().when(manager.refresh(any())).thenReturn(true);
        Sandbox sb = startingSandbox("sb-w", manager);
        AtomicBoolean started = new AtomicBoolean(false);
        when(sandboxApi.getSandbox("sb-w", null, null)).thenAnswer(inv ->
                TestSupport.mainSandbox("sb-w", started.get() ? SandboxState.STARTED : SandboxState.STARTING));
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread first = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(5), error));
        Thread second = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(5), error));

        first.start();
        second.start();
        verify(manager, timeout(2000).times(2)).subscribe(eq("sb-w"), any(), any());
        started.set(true);
        first.join(5000);
        second.join(5000);

        assertThat(error.get()).isNull();
        verify(manager).unsubscribe("sub-a");
        verify(manager).unsubscribe("sub-b");
    }

    @Test
    void pollCadenceIsFastAndUnjitteredWithoutASubscription() {
        java.util.function.DoubleSupplier random = () -> {
            throw new AssertionError("jitter must not apply without a subscription");
        };
        Sandbox.WaitPollCadence cadence = new Sandbox.WaitPollCadence(random);
        assertThat(cadence.millis(false, false)).isEqualTo(250);
        assertThat(cadence.millis(false, true)).isEqualTo(250);
    }

    @Test
    void pollCadenceIsFiveSecondsWithJitterWhenEventBacked() {
        assertThat(new Sandbox.WaitPollCadence(() -> 0.0).millis(true, true)).isEqualTo(4000);
        assertThat(new Sandbox.WaitPollCadence(() -> 0.5).millis(true, true)).isEqualTo(5000);
        assertThat(new Sandbox.WaitPollCadence(() -> 1.0).millis(true, true)).isEqualTo(6000);
    }

    @ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({"0.0, 0.8", "0.5, 1.0", "1.0, 1.2"})
    void pollCadenceBacksOffFromOneSecondByHalfUpToTenSecondsWhenNotEventBacked(double random, double factor) {
        Sandbox.WaitPollCadence cadence = new Sandbox.WaitPollCadence(() -> random);
        double[] base = {1000, 1500, 2250, 3375, 5062.5, 7593.75, 10_000, 10_000};
        for (double interval : base) {
            assertThat(cadence.millis(true, false)).isEqualTo(Math.round(interval * factor));
        }
    }

    @ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({"0.0, 800, 3000", "0.5, 1000, 3300", "1.0, 1200, 3600"})
    void aRateLimitedPollBacksOffAndRetryAfterGetsUpwardJitterOnly(double random, long plain, long retry) {
        // Without a subscription the fallback backoff still applies: max(fast 250 ms, 1 s x jitter).
        assertThat(new Sandbox.WaitPollCadence(() -> random).rateLimitedMillis(false, false, 0)).isEqualTo(plain);
        assertThat(new Sandbox.WaitPollCadence(() -> random).rateLimitedMillis(true, false, 3000)).isEqualTo(retry);
        // A Retry-After shorter than the interval changes nothing.
        assertThat(new Sandbox.WaitPollCadence(() -> random).rateLimitedMillis(true, false, 10)).isEqualTo(plain);
        // Event-backed: the 5 s interval wins over the 1 s fallback.
        assertThat(new Sandbox.WaitPollCadence(() -> random).rateLimitedMillis(true, true, 0)).isEqualTo(plain * 5);
    }

    @Test
    void everyRateLimitedPollTakesOneBackoffStep() {
        Sandbox.WaitPollCadence cadence = new Sandbox.WaitPollCadence(() -> 0.5);
        assertThat(List.of(cadence.rateLimitedMillis(false, false, 0), cadence.rateLimitedMillis(false, false, 0),
                cadence.rateLimitedMillis(false, false, 0))).containsExactly(1000L, 1500L, 2250L);
        assertThat(cadence.millis(true, false)).isEqualTo(3375);
    }

    @ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {
            "inf", "-inf", "nan", "NaN", "Infinity", "-Infinity", "-1", "-0.5", "1e400", "", "soon"})
    void retryAfterTreatsNonFiniteNegativeOrUnparseableValuesAsAbsent(String value) {
        DaytonaException limited = new DaytonaException(429, "slow down", Map.of("Retry-After", value));
        assertThat(Sandbox.retryAfterMillis(limited, 0)).isZero();
    }

    @ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({"3, 3000", "59.5, 59500", "60, 60000", "120, 60000",
            "99999999999999999999, 60000"})
    void retryAfterReadsSecondsCappedAtSixtySeconds(String value, long expected) {
        DaytonaException limited = new DaytonaException(429, "slow down", Map.of("Retry-After", value));
        assertThat(Sandbox.retryAfterMillis(limited, 0)).isEqualTo(expected);
    }

    @Test
    void retryAfterReadsHttpDatesCappedAtSixtySeconds() {
        long now = java.time.ZonedDateTime.parse("Wed, 21 Oct 2026 07:28:00 GMT",
                java.time.format.DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli();
        java.util.function.Function<String, Long> after = date -> Sandbox.retryAfterMillis(
                new DaytonaException(429, "slow down", Map.of("Retry-After", date)), now);
        assertThat(after.apply("Wed, 21 Oct 2026 07:28:05 GMT")).isEqualTo(5000);
        assertThat(after.apply("Wed, 21 Oct 2026 07:27:00 GMT")).isZero();
        assertThat(after.apply("Wed, 21 Oct 2026 09:00:00 GMT")).isEqualTo(60_000);
    }

    @Test
    void pollCadenceRestartsTheBackoffAfterAStretchOfBeingEventBacked() {
        Sandbox.WaitPollCadence cadence = new Sandbox.WaitPollCadence(() -> 0.5);
        assertThat(List.of(cadence.millis(true, false), cadence.millis(true, false), cadence.millis(true, false)))
                .containsExactly(1000L, 1500L, 2250L);
        assertThat(cadence.millis(true, true)).isEqualTo(5000);
        assertThat(List.of(cadence.millis(true, false), cadence.millis(true, false))).containsExactly(1000L, 1500L);
    }

    @Test
    void aNotEventBackedWaitFollowsTheBackoffAndRestartsItAfterBeingEventBacked() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        // Event-backed only on the 9th and 10th poll decision.
        java.util.concurrent.atomic.AtomicInteger decisions = new java.util.concurrent.atomic.AtomicInteger();
        when(manager.isEventBacked(any())).thenAnswer(inv -> {
            int n = decisions.incrementAndGet();
            return n == 9 || n == 10;
        });
        Sandbox sb = startingSandbox("sb-c", manager);
        // The wait's own cadence picks the intervals; the polls themselves run 1 ms apart, so no real waiting.
        List<Long> intervals = recordDecisions(sb, (decided, subscribed, eventBacked) -> 1);
        lenient().when(sandboxApi.getSandbox("sb-c", null, null)).thenAnswer(inv -> TestSupport.mainSandbox("sb-c",
                intervals.size() >= 12 ? SandboxState.STARTED : SandboxState.STARTING));

        sb.waitUntilStarted(30);

        assertThat(intervals.subList(0, 12)).containsExactly(
                1000L, 1500L, 2250L, 3375L, 5063L, 7594L, 10_000L, 10_000L, 5000L, 5000L, 1000L, 1500L);
    }

    @Test
    void aRejectedSubscriptionMakesItsWaitReadAtOnceThenPollAtTheFallbackCadence() throws Exception {
        try (FakeEventGateway gateway = new FakeEventGateway()) {
            EventDispatcher dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "test");
            EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
            try {
                dispatcher.ensureConnected();
                awaitCondition(dispatcher::isConnected);
                AtomicBoolean stopped = new AtomicBoolean(false);
                when(sandboxApi.stopSandbox("sb-r", null, null)).thenReturn(TestSupport.mainSandbox("sb-r", SandboxState.STOPPING));
                lenient().when(sandboxApi.getSandbox("sb-r", null, null)).thenAnswer(inv ->
                        TestSupport.mainSandbox("sb-r", stopped.get() ? SandboxState.STOPPED : SandboxState.STOPPING));
                Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-r", SandboxState.STARTED), () -> null, manager);
                // The wait's own cadence picks the intervals; event-backed polls are an hour apart, so a read
                // right after the rejection can only come from the rejection itself.
                List<String> decisions = new CopyOnWriteArrayList<>();
                sb.pollJitter = () -> 0.5;
                sb.pollScheduleHook = (decided, subscribed, eventBacked) -> {
                    decisions.add(eventBacked + ":" + decided);
                    return eventBacked ? TimeUnit.HOURS.toMillis(1) : 20;
                };
                AtomicReference<Throwable> error = new AtomicReference<>();
                Thread worker = new Thread(() -> runCapturing(() -> sb.stop(60), error));
                worker.start();

                FakeEventGateway.Emitted subscribe = gateway.takeEmitted(5000);
                assertSubscribed(subscribe, "subscribe", "sb-r");
                awaitCondition(() -> decisions.contains("true:5000"));
                verify(sandboxApi, never()).getSandbox("sb-r", null, null);

                gateway.ack(subscribe, "{\"ok\":false,\"error\":\"OVERLOADED\",\"message\":\"shedding\"}");

                verify(sandboxApi, timeout(2000)).getSandbox("sb-r", null, null);
                awaitCondition(() -> decisions.size() >= 3);
                assertThat(decisions.subList(0, 3)).containsExactly("true:5000", "false:1000", "false:1500");
                assertThat(dispatcher.isRejected("sb-r")).isTrue();
                assertThat(manager.isEventBacked("sb-r")).isFalse();

                stopped.set(true);
                worker.join(5000);
                assertThat(worker.isAlive()).isFalse();
                assertThat(error.get()).isNull();
                // A rejected id was never served at its release, so it sends no unsubscribe.
                assertThat(gateway.takeEmitted(400)).isNull();
            } finally {
                manager.shutdown();
                dispatcher.shutdown();
            }
        }
    }

    @ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({"0.0, 3000", "0.5, 3300", "1.0, 3600"})
    void aRateLimitedPollWaitsForRetryAfterPlusUpwardJitterAndTheWaitStillResolves(double jitter, long expected) {
        Sandbox sb = startingSandbox("sb-c", mockSubscriptionManager());
        sb.pollJitter = () -> jitter;
        List<Long> decisions = recordDecisions(sb, (decided, subscribed, eventBacked) -> 1);
        List<Integer> decisionsAtRead = new CopyOnWriteArrayList<>();
        when(sandboxApi.getSandbox("sb-c", null, null)).thenAnswer(inv -> {
            decisionsAtRead.add(decisions.size());
            if (decisionsAtRead.size() == 1) {
                throw new io.daytona.api.client.ApiException(429, Map.of("Retry-After", List.of("3")), "{\"message\":\"slow down\"}");
            }
            return TestSupport.mainSandbox("sb-c", SandboxState.STARTED);
        });

        sb.waitUntilStarted(30);

        assertThat(decisions).containsExactly(expected);
        assertThat(decisionsAtRead).containsExactly(0, 1);
    }

    @Test
    void aRateLimitedPollThatIsNotARateLimitExceptionStillBacksOff() {
        // A 429 the mapper did not turn into DaytonaRateLimitException still counts.
        DaytonaException limited = new DaytonaException(429, "slow down", Map.of("Retry-After", "2"));
        assertThat(Sandbox.isRateLimit(limited)).isTrue();
        assertThat(Sandbox.retryAfterMillis(limited, 0)).isEqualTo(2000);
    }

    @Test
    void aRateLimitedPollWithoutRetryAfterWaitsTheCurrentInterval() {
        Sandbox sb = startingSandbox("sb-c", mockSubscriptionManager());
        sb.pollJitter = () -> 0.5;
        List<Long> decisions = recordDecisions(sb, (decided, subscribed, eventBacked) -> 1);
        List<Integer> decisionsAtRead = new CopyOnWriteArrayList<>();
        when(sandboxApi.getSandbox("sb-c", null, null)).thenAnswer(inv -> {
            decisionsAtRead.add(decisions.size());
            if (decisionsAtRead.size() == 1) {
                throw new io.daytona.api.client.ApiException(429, Map.of(), "{\"message\":\"slow down\"}");
            }
            return TestSupport.mainSandbox("sb-c", decisionsAtRead.size() >= 3 ? SandboxState.STARTED : SandboxState.STARTING);
        });

        sb.waitUntilStarted(30);

        // The 429 kept the current 1 s interval and took its backoff step; one decision per read, no extra tick.
        assertThat(decisions).containsExactly(1000L, 1500L);
        assertThat(decisionsAtRead).containsExactly(0, 1, 2);
    }

    @Test
    void repeatedRateLimitedPollsBackOffEvenWithoutASubscription() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        lenient().when(manager.subscribe(any(), any(), any())).thenReturn(null);
        Sandbox sb = startingSandbox("sb-c", manager);
        sb.pollJitter = () -> 0.5;
        List<Long> decisions = recordDecisions(sb, (decided, subscribed, eventBacked) -> {
            assertThat(subscribed).isFalse();
            return 1;
        });
        java.util.concurrent.atomic.AtomicInteger reads = new java.util.concurrent.atomic.AtomicInteger();
        when(sandboxApi.getSandbox("sb-c", null, null)).thenAnswer(inv -> {
            int n = reads.incrementAndGet();
            if (n <= 9) {
                throw new io.daytona.api.client.ApiException(429, Map.of(), "{\"message\":\"slow down\"}");
            }
            return TestSupport.mainSandbox("sb-c", n >= 11 ? SandboxState.STARTED : SandboxState.STARTING);
        });

        sb.waitUntilStarted(30);

        // 1 s backing off to 10 s, then the 250 ms fast cadence again after a successful read.
        assertThat(decisions).containsExactly(1000L, 1500L, 2250L, 3375L, 5063L, 7594L, 10_000L, 10_000L, 10_000L, 250L);
    }

    @Test
    void aRateLimitedPollNeverAddsATickChainEvenWhenWokenMidWait() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-c", manager);
        // Every read is rate limited and the next poll is 200 ms away.
        sb.pollScheduleHook = (decided, subscribed, eventBacked) -> 200;
        List<Long> reads = new CopyOnWriteArrayList<>();
        java.util.concurrent.atomic.AtomicInteger inFlight = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger maxInFlight = new java.util.concurrent.atomic.AtomicInteger();
        lenient().when(sandboxApi.getSandbox("sb-c", null, null)).thenAnswer(inv -> {
            maxInFlight.accumulateAndGet(inFlight.incrementAndGet(), Math::max);
            reads.add(System.nanoTime());
            inFlight.decrementAndGet();
            throw new io.daytona.api.client.ApiException(429, Map.of(), "{\"message\":\"slow down\"}");
        });
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread waiter = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(0), error));
        waiter.start();
        awaitCondition(() -> reads.size() >= 2);
        long windowStart = System.nanoTime();
        int readsBefore = reads.size();
        // Rejections while the next tick is pending: each replaces that tick rather than adding a chain.
        for (int i = 0; i < 3; i++) {
            deliverRejection(manager, "sb-c");
            Thread.sleep(70);
        }
        Thread.sleep(2000 - 3 * 70);
        int readsInWindow = reads.size() - readsBefore;
        deliverStarted(manager, "sb-c");
        waiter.join(5000);

        assertThat(error.get()).isNull();
        assertThat(maxInFlight.get()).isEqualTo(1);
        // One chain at 200 ms gives at most ~11 reads in 2 s, plus one per wake; extra chains would give
        // 20 or more. Load only slows the scheduler down, so this bound cannot fail spuriously.
        assertThat(readsInWindow).as("reads in %d ms", TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - windowStart))
                .isLessThanOrEqualTo(15);
    }

    @Test
    void aRejectionDuringAReadAddsNoReadAndTheNextPollIsTheFallbackChosenAfterIt() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        // Event-backed until the gateway rejects the subscription.
        AtomicBoolean rejected = new AtomicBoolean(false);
        when(manager.isEventBacked(any())).thenAnswer(inv -> !rejected.get());
        Sandbox sb = startingSandbox("sb-c", manager);
        // Scheduled polls are an hour apart, so any second read could only come from the rejection.
        List<String> decisions = new CopyOnWriteArrayList<>();
        sb.pollJitter = () -> 0.5;
        sb.pollScheduleHook = (decided, subscribed, eventBacked) -> {
            decisions.add(eventBacked + ":" + decided);
            return TimeUnit.HOURS.toMillis(1);
        };
        java.util.concurrent.CountDownLatch firstReadStarted = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch releaseFirstRead = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.atomic.AtomicInteger reads = new java.util.concurrent.atomic.AtomicInteger();
        when(sandboxApi.getSandbox("sb-c", null, null)).thenAnswer(inv -> {
            if (reads.incrementAndGet() == 1) {
                firstReadStarted.countDown();
                assertThat(releaseFirstRead.await(10, TimeUnit.SECONDS)).isTrue();
            }
            return TestSupport.mainSandbox("sb-c", SandboxState.STARTING);
        });
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread waiter = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(0), error));
        waiter.start();

        assertThat(firstReadStarted.await(5, TimeUnit.SECONDS)).isTrue();
        rejected.set(true);
        deliverRejection(manager, "sb-c");
        releaseFirstRead.countDown();

        // The next poll was chosen after the read: the 1 s fallback, not the 5 s event-backed interval.
        awaitCondition(() -> !decisions.isEmpty());
        // No read follows the rejection: the one in flight covered it.
        verify(sandboxApi, org.mockito.Mockito.after(1000).times(1)).getSandbox("sb-c", null, null);
        deliverStarted(manager, "sb-c");
        waiter.join(5000);

        assertThat(error.get()).isNull();
        assertThat(decisions).containsExactly("false:1000");
    }

    @Test
    void stopReadsFirstWhenItsSubscriptionExpiredDuringTheRequest() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        // The request outlived the subscription's TTL: the manager no longer knows it.
        when(manager.refresh("mock-sub-id")).thenReturn(false);
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPING));
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2500);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
    }

    @Test
    void anErrorStateFromAnEventReportsTheReasonItCarriedWithoutARead() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPING));
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread worker = new Thread(() -> runCapturing(() -> sb.stop(30), error));
        worker.start();
        // The skip check and the first scheduled poll each renew the subscription.
        verify(manager, timeout(5000).times(2)).refresh("mock-sub-id");

        handlerOf(manager, "sb-s").accept("sandbox.state.updated", new ObjectMapper().readTree(
                "{\"sandbox\":{\"id\":\"sb-s\",\"state\":\"error\",\"errorReason\":\"disk full\"},\"newState\":\"error\"}"));
        worker.join(5000);

        assertThat(error.get()).hasMessage("Sandbox sb-s entered error state: error, error reason: disk full");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
    }

    @Test
    void anErrorStateFromAnEventWithoutItsReasonReadsTheSandboxFirst() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPING));
        io.daytona.api.client.model.Sandbox failed = TestSupport.mainSandbox("sb-s", SandboxState.ERROR);
        failed.setErrorReason("from the read");
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(failed);
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread worker = new Thread(() -> runCapturing(() -> sb.stop(30), error));
        worker.start();
        verify(manager, timeout(5000).times(2)).refresh("mock-sub-id");

        handlerOf(manager, "sb-s").accept("sandbox.state.updated", new ObjectMapper().readTree(
                "{\"sandbox\":{\"id\":\"sb-s\",\"state\":\"error\"},\"newState\":\"error\"}"));
        worker.join(5000);

        assertThat(error.get()).hasMessage("Sandbox sb-s entered error state: error, error reason: from the read");
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
    }

    /** Records each poll delay the wait's own cadence decides; {@code actual} picks the delay scheduled. */
    private static List<Long> recordDecisions(Sandbox sb, Sandbox.PollScheduleHook actual) {
        List<Long> decisions = new CopyOnWriteArrayList<>();
        if (sb.pollJitter == null) {
            sb.pollJitter = () -> 0.5;
        }
        sb.pollScheduleHook = (decided, subscribed, eventBacked) -> {
            decisions.add(decided);
            return actual.schedule(decided, subscribed, eventBacked);
        };
        return decisions;
    }

    private static BiConsumer<String, JsonNode> handlerOf(EventSubscriptionManager manager, String sandboxId) {
        @SuppressWarnings("unchecked")
        ArgumentCaptor<BiConsumer<String, JsonNode>> handler = ArgumentCaptor.forClass(BiConsumer.class);
        verify(manager, org.mockito.Mockito.atLeastOnce()).subscribe(eq(sandboxId), handler.capture(), any());
        return handler.getValue();
    }

    private static void deliverRejection(EventSubscriptionManager manager, String sandboxId) {
        handlerOf(manager, sandboxId).accept(EventDispatcher.SUBSCRIPTION_REJECTED_EVENT,
                new ObjectMapper().createObjectNode().put("id", sandboxId).put("error", "OVERLOADED"));
    }

    @Test
    void aServerErrorDuringAPollFailsTheWaitAsBefore() {
        Sandbox sb = startingSandbox("sb-c", mockSubscriptionManager());
        when(sandboxApi.getSandbox("sb-c", null, null))
                .thenThrow(new io.daytona.api.client.ApiException(500, "boom", Map.of(), "{\"message\":\"boom\"}"));

        assertThatThrownBy(() -> sb.waitUntilStarted(30)).isInstanceOf(DaytonaServerException.class).hasMessage("boom");
        verify(sandboxApi, times(1)).getSandbox("sb-c", null, null);
    }

    @Test
    void subscribedButDisconnectedWaitPollsAtAboutOneSecond() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        // One entry per poll tick (nanoTime); the manager mock reports the connection as down.
        List<Long> ticks = new CopyOnWriteArrayList<>();
        when(manager.isEventBacked(any())).thenAnswer(inv -> {
            ticks.add(System.nanoTime());
            return false;
        });
        Sandbox sb = startingSandbox("sb-c", manager);
        lenient().when(sandboxApi.getSandbox("sb-c", null, null)).thenReturn(TestSupport.mainSandbox("sb-c", SandboxState.STARTING));
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread waiter = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(60), error));

        waiter.start();
        awaitCondition(() -> ticks.size() >= 3);
        deliverStarted(manager, "sb-c");
        waiter.join(5000);

        assertThat(error.get()).isNull();
        assertThat(waiter.isAlive()).isFalse();
        // The scheduler never fires early, so this lower bound holds however loaded the machine is.
        List<Long> snapshot = List.copyOf(ticks);
        for (int i = 0; i + 1 < snapshot.size(); i++) {
            long gapMillis = TimeUnit.NANOSECONDS.toMillis(snapshot.get(i + 1) - snapshot.get(i));
            assertThat(gapMillis).as("gap after tick %d", i).isGreaterThanOrEqualTo(800);
        }
    }

    @Test
    void pollCadenceIsReevaluatedEachTickAndFollowsTheConnectionMidWait() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        AtomicBoolean connected = new AtomicBoolean(false);
        when(manager.isEventBacked(any())).thenAnswer(inv -> connected.get());
        Sandbox sb = startingSandbox("sb-c", manager);
        lenient().when(sandboxApi.getSandbox("sb-c", null, null)).thenReturn(TestSupport.mainSandbox("sb-c", SandboxState.STARTING));
        // One entry per scheduled poll: whether that tick saw a live connection. A connected tick
        // schedules the next poll an hour away, so no later tick can happen however slow the machine is.
        List<Boolean> ticks = new CopyOnWriteArrayList<>();
        sb.pollScheduleHook = (decided, subscribed, isConnected) -> {
            assertThat(subscribed).isTrue();
            ticks.add(isConnected);
            return isConnected ? TimeUnit.HOURS.toMillis(1) : 10;
        };
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread waiter = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(0), error));

        waiter.start();
        awaitCondition(() -> ticks.size() >= 3);
        connected.set(true);
        awaitCondition(() -> ticks.contains(true));
        deliverStarted(manager, "sb-c");
        waiter.join(5000);

        assertThat(error.get()).isNull();
        assertThat(waiter.isAlive()).isFalse();
        List<Boolean> snapshot = List.copyOf(ticks);
        assertThat(snapshot.subList(0, 3)).containsOnly(false);
        assertThat(snapshot.get(snapshot.size() - 1)).isTrue();
        assertThat(snapshot.stream().filter(Boolean::booleanValue).count()).isEqualTo(1);
    }

    @Test
    void stopWithTheConnectionUpResolvesFromTheStoppedEventWithoutAnyGet() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenAnswer(inv -> {
            verify(manager).subscribe(eq("sb-s"), any(), eq(List.of("sandbox.state.updated")));
            return TestSupport.mainSandbox("sb-s", SandboxState.STOPPING);
        });

        runAndDeliverOnceWaiting(manager, "sb-s", "stopped", () -> sb.stop(30));

        assertThat(sb.getState()).isEqualTo("stopped");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
        verify(manager, times(1)).subscribe(any(), any(), any());
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void stopWithTheConnectionDownWhenTheRequestIsSentReadsOnceImmediately() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        // Down while subscribing, up by the time the wait starts.
        when(manager.isEventBacked(any())).thenReturn(false, true);
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPING));
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2000);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void stopReadsImmediatelyWhenAStateEventArrivedDuringTheRequest() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenAnswer(inv -> {
            deliverState(manager, "sb-s", "stopped");
            return TestSupport.mainSandbox("sb-s", SandboxState.STOPPING);
        });
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2000);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
        assertThat(sb.getState()).isEqualTo("stopped");
    }

    @Test
    void stopReadsImmediatelyWhenTheConnectionDroppedAndReconnectedDuringTheRequest() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        java.util.concurrent.atomic.AtomicLong epoch = new java.util.concurrent.atomic.AtomicLong(1);
        when(manager.connectionEpoch()).thenAnswer(inv -> epoch.get());
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenAnswer(inv -> {
            // Down and up again while the request ran: the stopped event sent meanwhile is lost.
            epoch.incrementAndGet();
            return TestSupport.mainSandbox("sb-s", SandboxState.STOPPING);
        });
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        // Skipping the read would leave the wait until the first 4-6 s safety-net poll.
        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2500);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
        assertThat(sb.getState()).isEqualTo("stopped");
    }

    @Test
    void stopWhoseEmptyResponseIsFollowedByAnEmptyReadReadsAgainImmediately() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.getSandbox("sb-s", null, null))
                .thenReturn(null, TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2500);
        // The fallback read that returned nothing, then the wait's immediate read.
        verify(sandboxApi, times(2)).getSandbox("sb-s", null, null);
        assertThat(sb.getState()).isEqualTo("stopped");
    }

    @Test
    void deleteWithWaitAndAnEmptyResponseIsFollowedByAnEmptyReadReadsAgainImmediately() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-d", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.getSandbox("sb-d", null, null))
                .thenReturn(null, TestSupport.mainSandbox("sb-d", SandboxState.DESTROYED));

        long start = System.nanoTime();
        sb.delete(30, true);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2500);
        verify(sandboxApi, times(2)).getSandbox("sb-d", null, null);
        assertThat(sb.getState()).isEqualTo("destroyed");
    }

    @Test
    void stopReadsImmediatelyWhenAStateEventIsDeliveredFromInsideSubscribe() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        // The dispatcher thread can deliver an event as soon as the subscribe is in place.
        when(manager.subscribe(eq("sb-s"), any(), any())).thenAnswer(inv -> {
            BiConsumer<String, JsonNode> handler = inv.getArgument(1);
            handler.accept("sandbox.state.updated", new ObjectMapper().readTree(
                    "{\"sandbox\":{\"id\":\"sb-s\",\"state\":\"started\"},\"newState\":\"started\"}"));
            return "mock-sub-id";
        });
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPING));
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        // Skipping the read would leave the wait until the first 4-6 s safety-net poll.
        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2500);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
    }

    @Test
    void stopReadsImmediatelyWhenTheConnectionReconnectsInsideSubscribe() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        java.util.concurrent.atomic.AtomicLong epoch = new java.util.concurrent.atomic.AtomicLong(1);
        when(manager.connectionEpoch()).thenAnswer(inv -> epoch.get());
        when(manager.subscribe(eq("sb-s"), any(), any())).thenAnswer(inv -> {
            epoch.incrementAndGet();
            return "mock-sub-id";
        });
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPING));
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        long start = System.nanoTime();
        sb.stop(30);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2500);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
    }

    @Test
    void anOverflowWaitReadsFirstAndPollsAtTheSubscribedCadence() throws Exception {
        withCappedGateway((gateway, manager) -> {
            List<String> eventBacked = new CopyOnWriteArrayList<>();
            List<String> notEventBacked = new CopyOnWriteArrayList<>();
            Thread a = startStop(manager, "sb-a", eventBacked, notEventBacked);
            assertSubscribed(gateway.takeEmitted(5000), "subscribe", "sb-a");
            Thread b = startStop(manager, "sb-b", eventBacked, notEventBacked);
            assertSubscribed(gateway.takeEmitted(5000), "subscribe", "sb-b");
            Thread c = startStop(manager, "sb-c", eventBacked, notEventBacked);

            verify(sandboxApi, timeout(5000).atLeast(2)).getSandbox("sb-c", null, null);
            awaitCondition(() -> eventBacked.size() >= 2 && notEventBacked.contains("sb-c"));
            assertThat(gateway.takeEmitted(400)).isNull();
            assertThat(manager.isEventBacked("sb-c")).isFalse();
            assertThat(eventBacked).containsExactlyInAnyOrder("sb-a", "sb-b");
            assertThat(notEventBacked).containsOnly("sb-c");
            assertReadFirst("sb-c");
            // Not event-backed is the fallback cadence: 1 s x jitter first.
            assertThat(new Sandbox.WaitPollCadence(() -> 1.0).millis(true, false)).isEqualTo(1200);
            verify(sandboxApi, never()).getSandbox("sb-a", null, null);
            verify(sandboxApi, never()).getSandbox("sb-b", null, null);

            // The gateway sends nothing for an unserved id: sb-c ends through its poll.
            gateway.sendEvent("sandbox.state.updated", stoppedEvent("sb-a"));
            gateway.sendEvent("sandbox.state.updated", stoppedEvent("sb-b"));
            stoppedIds.add("sb-c");
            for (Thread t : List.of(a, b, c)) {
                t.join(5000);
                assertThat(t.isAlive()).isFalse();
            }
            assertThat(stopError.get()).isNull();
        });
    }

    @Test
    void aFreedSlotGoesToTheNextNewWaitNotToTheOverflowWait() throws Exception {
        withCappedGateway((gateway, manager) -> {
            List<String> eventBacked = new CopyOnWriteArrayList<>();
            List<String> notEventBacked = new CopyOnWriteArrayList<>();
            Thread a = startStop(manager, "sb-a", eventBacked, notEventBacked);
            assertSubscribed(gateway.takeEmitted(5000), "subscribe", "sb-a");
            Thread b = startStop(manager, "sb-b", eventBacked, notEventBacked);
            assertSubscribed(gateway.takeEmitted(5000), "subscribe", "sb-b");
            Thread c = startStop(manager, "sb-c", eventBacked, notEventBacked);
            verify(sandboxApi, timeout(5000)).getSandbox("sb-c", null, null);
            awaitCondition(() -> notEventBacked.contains("sb-c"));
            assertReadFirst("sb-c");

            gateway.sendEvent("sandbox.state.updated", stoppedEvent("sb-a"));
            a.join(5000);
            assertThat(a.isAlive()).isFalse();
            assertSubscribed(gateway.takeEmitted(5000), "unsubscribe", "sb-a");
            assertThat(manager.isEventBacked("sb-c")).isFalse();

            Thread d = startStop(manager, "sb-d", eventBacked, notEventBacked);
            assertSubscribed(gateway.takeEmitted(5000), "subscribe", "sb-d");
            awaitCondition(() -> eventBacked.contains("sb-d"));
            verify(sandboxApi, never()).getSandbox("sb-d", null, null);
            assertThat(manager.isEventBacked("sb-c")).isFalse();

            gateway.sendEvent("sandbox.state.updated", stoppedEvent("sb-b"));
            gateway.sendEvent("sandbox.state.updated", stoppedEvent("sb-d"));
            stoppedIds.add("sb-c");
            for (Thread t : List.of(b, c, d)) {
                t.join(5000);
                assertThat(t.isAlive()).isFalse();
            }
            // sb-c was never served, so its release sends no unsubscribe.
            List<String> unsubscribed = new ArrayList<>();
            for (int i = 0; i < 2; i++) {
                FakeEventGateway.Emitted message = gateway.takeEmitted(5000);
                assertThat(message).isNotNull();
                assertThat(message.event).isEqualTo("unsubscribe");
                unsubscribed.add(message.payload.path("sandboxIds").get(0).asText());
            }
            assertThat(unsubscribed).containsExactlyInAnyOrder("sb-b", "sb-d");
            assertThat(gateway.takeEmitted(400)).isNull();
            assertThat(stopError.get()).isNull();
        });
    }

    private interface CappedGatewayTest {
        void run(FakeEventGateway gateway, EventSubscriptionManager manager) throws Exception;
    }

    private static void withCappedGateway(CappedGatewayTest test) throws Exception {
        try (FakeEventGateway gateway = new FakeEventGateway()) {
            EventDispatcher dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "test", 2);
            EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
            try {
                dispatcher.ensureConnected();
                awaitCondition(dispatcher::isConnected);
                test.run(gateway, manager);
            } finally {
                manager.shutdown();
                dispatcher.shutdown();
            }
        }
    }

    // First failure of a stop started by startStop; JUnit creates a fresh instance per test.
    private final AtomicReference<Throwable> stopError = new AtomicReference<>();
    // Ids whose reads answer stopped from now on; the others answer stopping.
    private final Set<String> stoppedIds = java.util.concurrent.ConcurrentHashMap.newKeySet();
    // Threads on which each wait picked a poll delay.
    private final Map<String, List<String>> pollDelayThreads = new java.util.concurrent.ConcurrentHashMap<>();

    /**
     * A wait that reads first schedules its first poll at once and picks every later delay after a read,
     * on the refresh thread. One that skips the read picks its first delay up front, on the caller's thread.
     */
    private void assertReadFirst(String id) {
        assertThat(pollDelayThreads.get(id)).isNotEmpty().allMatch(name -> name.startsWith("daytona-sandbox-refresh"));
    }

    /**
     * Stops {@code id} on a new thread; its stop request answers {@code stopping}, and so do its reads
     * until the id is in {@link #stoppedIds}. Event-backed waits poll an hour apart, so they can only end
     * on an event; the others poll every 20 ms, as an unserved wait ends through its poll.
     */
    private Thread startStop(EventSubscriptionManager manager, String id,
                             List<String> eventBacked, List<String> notEventBacked) {
        lenient().when(sandboxApi.stopSandbox(id, null, null)).thenReturn(TestSupport.mainSandbox(id, SandboxState.STOPPING));
        lenient().when(sandboxApi.getSandbox(id, null, null)).thenAnswer(inv ->
                TestSupport.mainSandbox(id, stoppedIds.contains(id) ? SandboxState.STOPPED : SandboxState.STOPPING));
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox(id, SandboxState.STARTED), () -> null, manager);
        sb.pollScheduleHook = (decided, subscribed, isEventBacked) -> {
            (isEventBacked ? eventBacked : notEventBacked).add(id);
            pollDelayThreads.computeIfAbsent(id, k -> new CopyOnWriteArrayList<>()).add(Thread.currentThread().getName());
            return isEventBacked ? TimeUnit.HOURS.toMillis(1) : 20;
        };
        Thread worker = new Thread(() -> runCapturing(() -> sb.stop(60), stopError));
        worker.start();
        return worker;
    }

    private static String stoppedEvent(String id) {
        return "{\"sandbox\":{\"id\":\"" + id + "\",\"state\":\"stopped\"},\"newState\":\"stopped\"}";
    }

    private static void assertSubscribed(FakeEventGateway.Emitted message, String event, String id) {
        assertThat(message).as("expected %s %s", event, id).isNotNull();
        assertThat(message.event).isEqualTo(event);
        assertThat(message.payload.toString()).isEqualTo("{\"sandboxIds\":[\"" + id + "\"]}");
    }

    @Test
    void stopReleasesTheSubscriptionAndPropagatesTheErrorWhenTheRequestFails() throws Exception {
        try (FakeEventGateway gateway = new FakeEventGateway()) {
            EventDispatcher dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "test");
            EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
            try {
                // Connected before the request, so the subscribe and unsubscribe messages go out at once.
                dispatcher.ensureConnected();
                awaitCondition(dispatcher::isConnected);
                Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(),
                        TestSupport.mainSandbox("sb-f", SandboxState.STARTED), () -> null, manager);
                when(sandboxApi.stopSandbox("sb-f", null, null))
                        .thenThrow(new io.daytona.api.client.ApiException(500, "boom", null, "{\"message\":\"stop failed\"}"));

                assertThatThrownBy(() -> sb.stop(5))
                        .isInstanceOf(DaytonaServerException.class)
                        .hasMessage("stop failed");

                FakeEventGateway.Emitted subscribe = gateway.takeEmitted(5000);
                assertThat(subscribe).isNotNull();
                assertThat(subscribe.event).isEqualTo("subscribe");
                FakeEventGateway.Emitted unsubscribe = gateway.takeEmitted(5000);
                assertThat(unsubscribe).isNotNull();
                assertThat(unsubscribe.event).isEqualTo("unsubscribe");
                assertThat(unsubscribe.payload.toString()).isEqualTo("{\"sandboxIds\":[\"sb-f\"]}");
                verify(sandboxApi, never()).getSandbox(any(), any(), any());
            } finally {
                manager.shutdown();
                dispatcher.shutdown();
            }
        }
    }

    @Test
    void stopResolvesAtOnceAndReleasesTheSubscriptionWhenTheResponseIsTheTargetState() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.STOPPED));

        sb.stop(30);

        verify(sandboxApi, never()).getSandbox(any(), any(), any());
        verify(manager, times(1)).subscribe(any(), any(), any());
        verify(manager, times(1)).unsubscribe("mock-sub-id");
    }

    @Test
    void deleteWithWaitSkipsTheImmediateReadWhenTheSubscriptionWasLive() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-d", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.deleteSandbox("sb-d", null)).thenReturn(TestSupport.mainSandbox("sb-d", SandboxState.DESTROYING));

        runAndDeliverOnceWaiting(manager, "sb-d", "destroyed", () -> sb.delete(30, true));

        assertThat(sb.getState()).isEqualTo("destroyed");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void pauseSkipsTheImmediateReadWhenTheSubscriptionWasLive() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-p", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.pauseSandbox("sb-p", null)).thenReturn(TestSupport.mainSandbox("sb-p", SandboxState.PAUSING));

        runAndDeliverOnceWaiting(manager, "sb-p", "paused", () -> sb.pause(30));

        assertThat(sb.getState()).isEqualTo("paused");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void stopWhoseResponseIsAlreadyAnErrorReadsImmediatelyAndFailsAtOnce() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-s", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.stopSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.ERROR));
        when(sandboxApi.getSandbox("sb-s", null, null)).thenReturn(TestSupport.mainSandbox("sb-s", SandboxState.ERROR));

        long start = System.nanoTime();
        assertThatThrownBy(() -> sb.stop(30)).hasMessageContaining("Sandbox sb-s entered error state: error");

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2000);
        verify(sandboxApi, times(1)).getSandbox("sb-s", null, null);
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void forkWithALiveConnectionResolvesFromTheStartedEventWithoutAnyGet() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-1", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull()))
                .thenReturn(TestSupport.mainSandbox("sb-fork", SandboxState.STARTING));
        AtomicReference<Sandbox> forked = new AtomicReference<>();

        runAndDeliverOnceWaiting(manager, "sb-fork", "started", () -> forked.set(sb.fork(null, 30)));

        assertThat(forked.get().getId()).isEqualTo("sb-fork");
        assertThat(forked.get().getState()).isEqualTo("started");
        verify(sandboxApi, never()).getSandbox(any(), any(), any());
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void forkWhoseResponseLacksTheToolboxProxyUrlReadsOnceImmediately() throws Exception {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-1", SandboxState.STARTED), () -> null, manager);
        io.daytona.api.client.model.Sandbox forkResponse = TestSupport.mainSandbox("sb-fork", SandboxState.STARTING);
        forkResponse.setToolboxProxyUrl(null);
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull())).thenReturn(forkResponse);
        when(sandboxApi.getToolboxProxyUrl("sb-fork", null)).thenReturn(new ToolboxProxyUrl().url("https://proxy.example"));
        when(sandboxApi.getSandbox("sb-fork", null, null)).thenReturn(TestSupport.mainSandbox("sb-fork", SandboxState.STARTING));
        AtomicReference<Sandbox> forked = new AtomicReference<>();

        // The event comes only after the wait's immediate read, which therefore must not be skipped.
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread worker = new Thread(() -> runCapturing(() -> forked.set(sb.fork(null, 30)), error));
        worker.start();
        // A skipped read would leave the first GET to the safety-net poll, no earlier than 4 s.
        verify(sandboxApi, timeout(2500)).getSandbox("sb-fork", null, null);
        deliverState(manager, "sb-fork", "started");
        worker.join(5000);

        assertThat(error.get()).isNull();
        assertThat(worker.isAlive()).isFalse();
        assertThat(forked.get().getState()).isEqualTo("started");
        verify(sandboxApi).getToolboxProxyUrl("sb-fork", null);
        verify(sandboxApi, times(1)).getSandbox("sb-fork", null, null);
    }

    @Test
    void forkWithoutALiveConnectionReadsOnceImmediately() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        // Down while subscribing, up by the time the wait starts.
        when(manager.isEventBacked(any())).thenReturn(false, true);
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-1", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull()))
                .thenReturn(TestSupport.mainSandbox("sb-fork", SandboxState.STARTING));
        when(sandboxApi.getSandbox("sb-fork", null, null)).thenReturn(TestSupport.mainSandbox("sb-fork", SandboxState.STARTED));

        long start = System.nanoTime();
        Sandbox forked = sb.fork(null, 30);

        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(2000);
        assertThat(forked.getState()).isEqualTo("started");
        verify(sandboxApi, times(1)).getSandbox("sb-fork", null, null);
        verify(manager).unsubscribe("mock-sub-id");
    }

    @Test
    void forkWhoseResponseIsAlreadyStartedTakesNoWaitAndNoSubscription() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-1", SandboxState.STARTED), () -> null, manager);
        when(sandboxApi.forkSandbox(eq("sb-1"), any(ForkSandbox.class), isNull()))
                .thenReturn(TestSupport.mainSandbox("sb-fork", SandboxState.STARTED));

        sb.fork(null, 30);

        verify(sandboxApi, never()).getSandbox(any(), any(), any());
        verify(manager, never()).subscribe(any(), any(), any());
    }

    @Test
    void deleteWithoutWaitTakesNoSubscription() {
        EventSubscriptionManager manager = connectedSubscriptionManager();
        Sandbox sb = new Sandbox(sandboxApi, TestSupport.config(), TestSupport.mainSandbox("sb-d", SandboxState.STARTED), () -> null, manager);

        sb.delete(5);

        verify(sandboxApi).deleteSandbox("sb-d", null);
        verify(manager, never()).subscribe(any(), any(), any());
    }

    /** Runs {@code action} on a thread and delivers {@code state} once its wait has started polling. */
    private void runAndDeliverOnceWaiting(EventSubscriptionManager manager, String sandboxId, String state,
                                          Runnable action) throws Exception {
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread worker = new Thread(() -> runCapturing(action, error));
        worker.start();
        // The wait checks its subscription before skipping its first read, then renews it when it schedules
        // its first poll.
        verify(manager, timeout(5000).times(2)).refresh("mock-sub-id");
        deliverState(manager, sandboxId, state);
        worker.join(5000);
        assertThat(error.get()).isNull();
        assertThat(worker.isAlive()).isFalse();
    }

    private void deliverState(EventSubscriptionManager manager, String sandboxId, String state) throws Exception {
        @SuppressWarnings("unchecked")
        ArgumentCaptor<BiConsumer<String, JsonNode>> handler = ArgumentCaptor.forClass(BiConsumer.class);
        verify(manager).subscribe(eq(sandboxId), handler.capture(), any());
        handler.getValue().accept("sandbox.state.updated", new ObjectMapper().readTree(
                "{\"sandbox\":{\"id\":\"" + sandboxId + "\",\"state\":\"" + state + "\"},"
                        + "\"newState\":\"" + state + "\"}"));
    }

    private EventSubscriptionManager connectedSubscriptionManager() {
        EventSubscriptionManager manager = mockSubscriptionManager();
        lenient().when(manager.isEventBacked(any())).thenReturn(true);
        return manager;
    }

    private void deliverStarted(EventSubscriptionManager manager, String sandboxId) throws Exception {
        @SuppressWarnings("unchecked")
        ArgumentCaptor<BiConsumer<String, JsonNode>> handler = ArgumentCaptor.forClass(BiConsumer.class);
        verify(manager).subscribe(eq(sandboxId), handler.capture(), any());
        handler.getValue().accept("sandbox.state.updated", new ObjectMapper().readTree(
                "{\"sandbox\":{\"id\":\"" + sandboxId + "\",\"state\":\"started\"},"
                        + "\"oldState\":\"starting\",\"newState\":\"started\"}"));
    }

    @Test
    void stateEventResolvesWaitAndAppliesOnlyTheStateAndErrorReason() throws Exception {
        EventSubscriptionManager manager = mockSubscriptionManager();
        Sandbox sb = startingSandbox("sb-e", manager);
        lenient().when(sandboxApi.getSandbox("sb-e", null, null)).thenReturn(TestSupport.mainSandbox("sb-e", SandboxState.STARTING));
        AtomicReference<Throwable> error = new AtomicReference<>();
        Thread waiter = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(10), error));

        waiter.start();
        @SuppressWarnings("unchecked")
        ArgumentCaptor<BiConsumer<String, JsonNode>> handler = ArgumentCaptor.forClass(BiConsumer.class);
        verify(manager, timeout(2000)).subscribe(eq("sb-e"), handler.capture(), any());
        handler.getValue().accept("sandbox.state.updated", new ObjectMapper().readTree(
                "{\"sandbox\":{\"id\":\"sb-e\",\"state\":\"started\",\"cpu\":64,\"errorReason\":\"from-event\"},"
                        + "\"oldState\":\"starting\",\"newState\":\"started\"}"));
        waiter.join(5000);

        assertThat(error.get()).isNull();
        assertThat(waiter.isAlive()).isFalse();
        // Only the state and, since the event carries one, the error reason; never the other fields.
        assertThat(sb.getCpu()).isEqualTo(1);
        assertThat(sb.getErrorReason()).isEqualTo("from-event");
    }

    @Test
    void waitOpensTheEventConnectionAndSubscribesOnlyForItsDuration() throws Exception {
        try (FakeEventGateway gateway = new FakeEventGateway()) {
            EventDispatcher dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "test");
            EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
            try {
                lenient().when(sandboxApi.getSandbox("sb-g", null, null))
                        .thenReturn(TestSupport.mainSandbox("sb-g", SandboxState.STARTING));
                Sandbox sb = startingSandbox("sb-g", manager);
                Thread.sleep(200);
                assertThat(gateway.requestCount()).isZero();

                AtomicReference<Throwable> error = new AtomicReference<>();
                Thread waiter = new Thread(() -> runCapturing(() -> sb.waitUntilStarted(10), error));
                waiter.start();

                FakeEventGateway.Emitted subscribe = gateway.takeEmitted(5000);
                assertThat(subscribe).isNotNull();
                assertThat(subscribe.event).isEqualTo("subscribe");
                assertThat(subscribe.payload.toString()).isEqualTo("{\"sandboxIds\":[\"sb-g\"]}");

                gateway.sendEvent("sandbox.state.updated",
                        "{\"sandbox\":{\"id\":\"sb-g\",\"state\":\"started\",\"desiredState\":\"started\"},"
                                + "\"oldState\":\"starting\",\"newState\":\"started\"}");
                waiter.join(5000);
                assertThat(error.get()).isNull();
                assertThat(sb.getState()).isEqualTo("started");

                FakeEventGateway.Emitted unsubscribe = gateway.takeEmitted(5000);
                assertThat(unsubscribe).isNotNull();
                assertThat(unsubscribe.event).isEqualTo("unsubscribe");
                assertThat(unsubscribe.payload.toString()).isEqualTo("{\"sandboxIds\":[\"sb-g\"]}");
            } finally {
                manager.shutdown();
                dispatcher.shutdown();
            }
        }
    }

    private static void awaitCondition(java.util.function.BooleanSupplier condition) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
        while (!condition.getAsBoolean()) {
            assertThat(System.nanoTime()).as("condition not met within 20 s").isLessThan(deadline);
            Thread.sleep(10);
        }
    }

    private Sandbox startingSandbox(String id, EventSubscriptionManager manager) {
        return new Sandbox(sandboxApi, TestSupport.config(),
                TestSupport.mainSandbox(id, SandboxState.STARTING), () -> null, manager);
    }

    private static void runCapturing(Runnable action, AtomicReference<Throwable> error) {
        try {
            action.run();
        } catch (Throwable e) {
            error.set(e);
        }
    }

    @Test
    void waitUntilStartedReportsTheErrorReasonCarriedByTheStateEvent() throws Exception {
        JsonNode event = new ObjectMapper().readTree("{\"sandbox\": {\"id\": \"sb-1\", \"state\": \"error\", "
                + "\"desiredState\": \"started\", \"errorReason\": \"disk full\"}, "
                + "\"oldState\": \"starting\", \"newState\": \"error\"}");
        Method handleEvent = Sandbox.class.getDeclaredMethod("handleEvent", String.class, JsonNode.class);
        handleEvent.setAccessible(true);
        // The event arrives while a poll is in flight; the poll then returns the earlier state.
        when(sandboxApi.getSandbox("sb-1", null, null)).thenAnswer(invocation -> {
            handleEvent.invoke(sandbox, "sandbox.state.updated", event);
            TestSupport.setField(sandbox, "errorReason", null);
            return TestSupport.mainSandbox("sb-1", SandboxState.STARTING);
        });
        TestSupport.setField(sandbox, "state", "starting");

        assertThatThrownBy(() -> sandbox.waitUntilStarted(10))
                .hasMessage("Sandbox sb-1 entered error state: error, error reason: disk full");
    }

    @Test
    void stateEventWithoutAnErrorReasonClearsIt() throws Exception {
        TestSupport.setField(sandbox, "state", "error");
        TestSupport.setField(sandbox, "errorReason", "transient");
        Method handleEvent = Sandbox.class.getDeclaredMethod("handleEvent", String.class, JsonNode.class);
        handleEvent.setAccessible(true);

        handleEvent.invoke(sandbox, "sandbox.state.updated", new ObjectMapper().readTree(
                "{\"sandbox\": {\"id\": \"sb-1\", \"state\": \"started\"}, \"oldState\": \"error\", "
                        + "\"newState\": \"started\"}"));

        assertThat(sandbox.getState()).isEqualTo("started");
        assertThat(sandbox.getErrorReason()).isNull();
    }

    private EventSubscriptionManager mockSubscriptionManager() {
        EventSubscriptionManager mockManager = mock(EventSubscriptionManager.class);
        lenient().when(mockManager.subscribe(any(), any(), any())).thenReturn("mock-sub-id");
        lenient().when(mockManager.refresh("mock-sub-id")).thenReturn(true);
        return mockManager;
    }
}
