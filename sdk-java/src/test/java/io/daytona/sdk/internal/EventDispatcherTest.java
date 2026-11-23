// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.internal;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.HttpUrl;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

import static org.assertj.core.api.Assertions.assertThat;

class EventDispatcherTest {

    private static final List<String> STATE_EVENTS = List.of("sandbox.state.updated");
    private static final long WAIT_MILLIS = 5000;
    private static final long QUIET_MILLIS = 400;
    private static final BiConsumer<String, JsonNode> NOOP = (event, data) -> { };

    private FakeEventGateway gateway;
    private EventDispatcher dispatcher;

    @BeforeEach
    void setUp() throws Exception {
        gateway = new FakeEventGateway();
        dispatcher = new EventDispatcher(gateway.apiUrl(), "test-token", null, "sdk-java", "1.2.3");
    }

    @AfterEach
    void tearDown() throws Exception {
        dispatcher.shutdown();
        gateway.close();
    }

    @Test
    void constructionOpensNoConnection() throws Exception {
        Thread.sleep(QUIET_MILLIS);

        assertThat(gateway.requestCount()).isZero();
        assertThat(dispatcher.isConnected()).isFalse();
    }

    @Test
    void firstSubscribeConnectsInExplicitModeAndSubscribesTheId() throws Exception {
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);

        RecordedRequest handshake = gateway.takeHandshake(WAIT_MILLIS);
        assertThat(handshake).isNotNull();
        HttpUrl url = handshake.getRequestUrl();
        assertThat(url.encodedPath()).isEqualTo("/api/socket.io/");
        assertThat(url.queryParameter("subscriptionMode")).isEqualTo("explicit");
        assertThat(url.queryParameter("source")).isEqualTo("sdk-java");
        assertThat(url.queryParameter("sdkVersion")).isEqualTo("1.2.3");

        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void sendsSubscribeOnFirstListenerAndUnsubscribeOnLastWhileConnected() throws Exception {
        Runnable first = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(dispatcher.isConnected()).isTrue();

        Runnable second = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        Runnable other = dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();

        first.run();
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();

        second.run();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "unsubscribe", Set.of("sb-1"));

        other.run();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "unsubscribe", Set.of("sb-2"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void whileDisconnectedOnlyTracksIdsThenSendsTheWholeSetInChunksOnConnect() throws Exception {
        gateway.holdConnects();

        Set<String> expected = new HashSet<>();
        Runnable removed = null;
        for (int i = 0; i < 250; i++) {
            String id = "sb-" + i;
            Runnable unsubscribe = dispatcher.subscribe(id, NOOP, STATE_EVENTS);
            if (i == 7) {
                removed = unsubscribe;
            } else {
                expected.add(id);
            }
        }
        removed.run();

        assertThat(gateway.takeHandshake(WAIT_MILLIS)).isNotNull();
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        gateway.resumeAccepting();

        List<Integer> chunkSizes = new ArrayList<>();
        List<String> received = new ArrayList<>();
        while (received.size() < expected.size()) {
            FakeEventGateway.Emitted message = gateway.takeEmitted(WAIT_MILLIS);
            assertThat(message).isNotNull();
            assertThat(message.event).isEqualTo("subscribe");
            List<String> ids = ids(message);
            chunkSizes.add(ids.size());
            received.addAll(ids);
        }

        assertThat(received).containsExactlyInAnyOrderElementsOf(expected);
        assertThat(chunkSizes).containsExactly(100, 100, 49);
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void resendsTheWholeSetAfterReconnectWithoutBufferedDuplicates() throws Exception {
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));

        gateway.holdConnects();
        gateway.dropConnection();
        waitUntil(() -> !dispatcher.isConnected());

        Runnable removed = dispatcher.subscribe("sb-3", NOOP, STATE_EVENTS);
        dispatcher.subscribe("sb-4", NOOP, STATE_EVENTS);
        removed.run();
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        gateway.resumeAccepting();

        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1", "sb-2", "sb-4"));
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
    }

    @Test
    void connectionEpochAdvancesOnConnectAndOnEveryReconnect() throws Exception {
        EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
        assertThat(manager.connectionEpoch()).isZero();
        manager.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        long afterConnect = manager.connectionEpoch();
        assertThat(afterConnect).isPositive();

        gateway.holdConnects();
        gateway.dropConnection();
        waitUntil(() -> !dispatcher.isConnected());
        assertThat(manager.connectionEpoch()).isEqualTo(afterConnect);
        gateway.resumeAccepting();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));

        assertThat(manager.connectionEpoch()).isGreaterThan(afterConnect);
        assertThat(new EventSubscriptionManager(null).connectionEpoch()).isZero();
        manager.shutdown();
    }

    @Test
    void isNotEventBackedWhileDisconnectedOrAfterShutdown() throws Exception {
        EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
        gateway.holdConnects();
        manager.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertThat(manager.isEventBacked("sb-1")).isFalse();

        gateway.resumeAccepting();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(manager.isEventBacked("sb-1")).isTrue();

        manager.shutdown();
        assertThat(manager.isEventBacked("sb-1")).isFalse();
        assertThat(new EventSubscriptionManager(null).isEventBacked("sb-1")).isFalse();
    }

    @Test
    void anIdStillServedWhileTheConnectionIsDownIsNotEventBacked() throws Exception {
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(dispatcher.isEventBacked("sb-1")).isTrue();

        gateway.holdConnects();
        gateway.dropConnection();
        waitUntil(() -> !dispatcher.isConnected());

        assertThat(dispatcher.isServed("sb-1")).isTrue();
        assertThat(dispatcher.isEventBacked("sb-1")).isFalse();
    }

    @Test
    void theManagerDelegatesToTheDispatchersAtomicCheck() {
        // Separate reads can see "connected" and "served" from either side of a disconnect.
        EventDispatcher stub = org.mockito.Mockito.mock(EventDispatcher.class);
        org.mockito.Mockito.lenient().when(stub.isConnected()).thenReturn(true);
        org.mockito.Mockito.lenient().when(stub.isServed("sb-1")).thenReturn(true);
        org.mockito.Mockito.when(stub.isEventBacked("sb-1")).thenReturn(false);

        assertThat(new EventSubscriptionManager(stub).isEventBacked("sb-1")).isFalse();
    }

    @Test
    void hasNoClientSideLimitEveryIdPastTheOldTenThousandIsSubscribedInChunks() throws Exception {
        int count = 10_101;
        // Subscribed while the connect is held, so the connect sends the whole served set in chunks.
        gateway.holdConnects();
        for (int i = 0; i < count; i++) {
            dispatcher.subscribe("sb-" + i, NOOP, STATE_EVENTS);
        }
        gateway.resumeAccepting();

        // Wait for each expected chunk, so slow emission cannot cut the count short.
        List<String> sent = new ArrayList<>();
        while (sent.size() < count) {
            FakeEventGateway.Emitted message = gateway.takeEmitted(WAIT_MILLIS);
            assertThat(message).as("subscribe messages for every id").isNotNull();
            assertThat(message.event).isEqualTo("subscribe");
            assertThat(ids(message)).hasSizeLessThanOrEqualTo(100);
            sent.addAll(ids(message));
        }
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        Set<String> expected = new HashSet<>();
        for (int i = 0; i < count; i++) {
            expected.add("sb-" + i);
        }
        // Exactly sb-0..sb-10100, each once.
        assertThat(sent).hasSize(count);
        assertThat(new HashSet<>(sent)).isEqualTo(expected);
        assertThat(dispatcher.isServed("sb-" + (count - 1))).isTrue();
    }

    @Test
    void sendsEverySubscribeWithAnAcknowledgementAndEveryUnsubscribeWithout() throws Exception {
        Runnable release = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted subscribe = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(subscribe, "subscribe", Set.of("sb-1"));
        assertThat(subscribe.ackId).isNotNull();

        release.run();
        FakeEventGateway.Emitted unsubscribe = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(unsubscribe, "unsubscribe", Set.of("sb-1"));
        assertThat(unsubscribe.ackId).isNull();
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"OVERLOADED", "SUBSCRIPTION_LIMIT_EXCEEDED"})
    void aRejectionUnservesTheIdMarksItRejectedTellsItsHandlersAndKeepsItOutOfTheReconnectSet(String code)
            throws Exception {
        EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
        try {
            BlockingQueue<String> notified = new LinkedBlockingQueue<>();
            dispatcher.subscribe("sb-1", (event, data) -> notified.add(event + " " + data.path("error").asText()), STATE_EVENTS);
            FakeEventGateway.Emitted first = gateway.takeEmitted(WAIT_MILLIS);
            assertEmitted(first, "subscribe", Set.of("sb-1"));
            dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
            assertThat(manager.isEventBacked("sb-1")).isTrue();

            gateway.ack(first, "{\"ok\":false,\"error\":\"" + code + "\",\"message\":\"no\"}");

            assertThat(notified.poll(WAIT_MILLIS, TimeUnit.MILLISECONDS))
                    .isEqualTo(EventDispatcher.SUBSCRIPTION_REJECTED_EVENT + " " + code);
            assertThat(dispatcher.isServed("sb-1")).isFalse();
            assertThat(dispatcher.isRejected("sb-1")).isTrue();
            assertThat(manager.isEventBacked("sb-1")).isFalse();
            assertThat(dispatcher.isServed("sb-2")).isTrue();

            gateway.dropConnection();
            assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-2"));
            assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
            assertThat(dispatcher.isRejected("sb-1")).isTrue();
        } finally {
            manager.shutdown();
        }
    }

    // Shortened acknowledgement timeout for these tests; they wait for the timer itself, not a fixed time.
    private static final long ACK_TIMEOUT_MILLIS = 1000;

    @Test
    void aLateRejectionOfAnEarlierSubscriptionDoesNotUnserveANewerOneOfTheSameId() throws Exception {
        EventSubscriptionManager manager = new EventSubscriptionManager(dispatcher);
        Runnable releaseFirst = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted first = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(first, "subscribe", Set.of("sb-1"));
        releaseFirst.run();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "unsubscribe", Set.of("sb-1"));
        BlockingQueue<String> notified = new LinkedBlockingQueue<>();
        dispatcher.subscribe("sb-1", (event, data) -> notified.add(event), STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));

        gateway.ack(first, "{\"ok\":false,\"error\":\"OVERLOADED\"}");

        assertThat(notified.poll(QUIET_MILLIS, TimeUnit.MILLISECONDS)).isNull();
        assertThat(dispatcher.isServed("sb-1")).isTrue();
        assertThat(dispatcher.isRejected("sb-1")).isFalse();
        assertThat(manager.isEventBacked("sb-1")).isTrue();
        manager.shutdown();
    }

    @Test
    void aConnectionWhoseFirstAcknowledgedSubscribeGetsNoAckSendsTheRestWithoutOneUntilItReconnects() throws Exception {
        dispatcher.subscribeAckTimeoutMillis = ACK_TIMEOUT_MILLIS;
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted first = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(first, "subscribe", Set.of("sb-1"));
        assertThat(first.ackId).isNotNull();
        waitUntil(dispatcher::ackProbeDone);
        assertThat(dispatcher.acksSilent()).isTrue();

        dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted second = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(second, "subscribe", Set.of("sb-2"));
        assertThat(second.ackId).as("fire-and-forget after the silent timeout").isNull();

        // A reconnect asks again, and a late acknowledgement from the old connection changes nothing.
        gateway.dropConnection();
        FakeEventGateway.Emitted resent = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(resent, "subscribe", Set.of("sb-1", "sb-2"));
        assertThat(resent.ackId).isNotNull();
        gateway.ack(first, "{\"ok\":false,\"error\":\"OVERLOADED\"}");
        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        assertThat(dispatcher.isRejected("sb-1")).isFalse();
        assertThat(dispatcher.isServed("sb-1")).isTrue();
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"{\"ok\":true}", "{\"ok\":false,\"error\":\"RATE_LIMITED\"}"})
    void anyAcknowledgementKeepsTheConnectionAskingForThem(String response) throws Exception {
        dispatcher.subscribeAckTimeoutMillis = ACK_TIMEOUT_MILLIS;
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted first = gateway.takeEmitted(WAIT_MILLIS);
        gateway.ack(first, response);
        waitUntil(dispatcher::ackProbeDone);
        assertThat(dispatcher.acksSilent()).isFalse();

        dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted second = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(second, "subscribe", Set.of("sb-2"));
        assertThat(second.ackId).isNotNull();
    }

    @Test
    void aLateFirstAcknowledgementSwitchesAcknowledgementsBackOnForTheConnection() throws Exception {
        dispatcher.subscribeAckTimeoutMillis = ACK_TIMEOUT_MILLIS;
        dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted first = gateway.takeEmitted(WAIT_MILLIS);
        waitUntil(dispatcher::ackProbeDone);
        dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        assertThat(gateway.takeEmitted(WAIT_MILLIS).ackId).isNull();

        gateway.ack(first, "{\"ok\":true}");
        waitUntil(() -> !dispatcher.acksSilent());
        dispatcher.subscribe("sb-3", NOOP, STATE_EVENTS);

        FakeEventGateway.Emitted third = gateway.takeEmitted(WAIT_MILLIS);
        assertEmitted(third, "subscribe", Set.of("sb-3"));
        assertThat(third.ackId).isNotNull();
    }

    @Test
    void aLateAcknowledgementAfterTheSwitchIsStillHandled() throws Exception {
        dispatcher.subscribeAckTimeoutMillis = ACK_TIMEOUT_MILLIS;
        BlockingQueue<String> notified = new LinkedBlockingQueue<>();
        dispatcher.subscribe("sb-1", (event, data) -> notified.add(event), STATE_EVENTS);
        FakeEventGateway.Emitted first = gateway.takeEmitted(WAIT_MILLIS);
        waitUntil(dispatcher::ackProbeDone);
        dispatcher.subscribe("sb-2", NOOP, STATE_EVENTS);
        assertThat(gateway.takeEmitted(WAIT_MILLIS).ackId).isNull();

        gateway.ack(first, "{\"ok\":false,\"error\":\"OVERLOADED\"}");

        assertThat(notified.poll(WAIT_MILLIS, TimeUnit.MILLISECONDS)).isEqualTo(EventDispatcher.SUBSCRIPTION_REJECTED_EVENT);
        assertThat(dispatcher.isRejected("sb-1")).isTrue();
        assertThat(dispatcher.isServed("sb-1")).isFalse();
    }

    @Test
    void aRejectedIdSendsNoUnsubscribeAndIsForgottenWhenItsLastListenerGoes() throws Exception {
        Runnable release = dispatcher.subscribe("sb-1", NOOP, STATE_EVENTS);
        FakeEventGateway.Emitted subscribe = gateway.takeEmitted(WAIT_MILLIS);
        gateway.ack(subscribe, "{\"ok\":false,\"error\":\"RATE_LIMITED\"}");
        waitUntil(() -> dispatcher.isRejected("sb-1"));

        release.run();

        assertThat(gateway.takeEmitted(QUIET_MILLIS)).isNull();
        assertThat(dispatcher.isRejected("sb-1")).isFalse();
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(strings = {"{\"ok\":true}", "\"ok\"", ""})
    void anAcceptedMalformedOrMissingAcknowledgementChangesNothing(String response) throws Exception {
        BlockingQueue<String> notified = new LinkedBlockingQueue<>();
        dispatcher.subscribe("sb-1", (event, data) -> notified.add(event), STATE_EVENTS);
        FakeEventGateway.Emitted subscribe = gateway.takeEmitted(WAIT_MILLIS);
        if (!response.isEmpty()) {
            gateway.ack(subscribe, response);
        }

        assertThat(notified.poll(QUIET_MILLIS, TimeUnit.MILLISECONDS)).isNull();
        assertThat(dispatcher.isServed("sb-1")).isTrue();
        assertThat(dispatcher.isRejected("sb-1")).isFalse();
        assertThat(dispatcher.isEventBacked("sb-1")).isTrue();
    }

    @Test
    void aStaleConnectCallbackFromAReplacedSocketChangesNothing() throws Exception {
        BlockingQueue<JsonNode> received = new LinkedBlockingQueue<>();
        dispatcher.subscribe("sb-1", (event, data) -> received.add(data), STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        io.socket.client.Socket oldSocket = privateField("socket", io.socket.client.Socket.class);
        // The old socket's connect callback, as if it had been dispatched just before the replacement.
        List<io.socket.emitter.Emitter.Listener> staleConnect =
                List.copyOf(oldSocket.listeners(io.socket.client.Socket.EVENT_CONNECT));
        assertThat(staleConnect).isNotEmpty();

        gateway.holdConnects();
        gateway.dropConnection();
        waitUntil(() -> !dispatcher.isConnected());
        dispatcher.connect();
        assertThat(privateField("socket", io.socket.client.Socket.class)).isNotSameAs(oldSocket);
        long epoch = dispatcher.connectionEpoch();
        Set<?> bound = privateField("socketBoundEvents", Set.class);
        assertThat(bound).isEmpty();

        for (io.socket.emitter.Emitter.Listener listener : staleConnect) {
            listener.call();
        }

        assertThat(bound).isEmpty();
        assertThat(dispatcher.isConnected()).isFalse();
        assertThat(dispatcher.connectionEpoch()).isEqualTo(epoch);

        gateway.resumeAccepting();
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));
        assertThat(dispatcher.isConnected()).isTrue();
        assertThat(dispatcher.connectionEpoch()).isEqualTo(epoch + 1);
        gateway.sendEvent("sandbox.state.updated",
                "{\"sandbox\":{\"id\":\"sb-1\",\"state\":\"started\"},\"newState\":\"started\"}");
        JsonNode data = received.poll(WAIT_MILLIS, TimeUnit.MILLISECONDS);
        assertThat(data).as("event delivered on the current socket").isNotNull();
        assertThat(data.path("newState").asText()).isEqualTo("started");
    }

    private <T> T privateField(String name, Class<T> type) throws ReflectiveOperationException {
        java.lang.reflect.Field field = EventDispatcher.class.getDeclaredField(name);
        field.setAccessible(true);
        return type.cast(field.get(dispatcher));
    }

    @Test
    void dispatchesSlimStatePayloadOnlyToMatchingId() throws Exception {
        BlockingQueue<JsonNode> received = new LinkedBlockingQueue<>();
        dispatcher.subscribe("sb-1", (event, data) -> received.add(data), STATE_EVENTS);
        assertEmitted(gateway.takeEmitted(WAIT_MILLIS), "subscribe", Set.of("sb-1"));

        gateway.sendEvent("sandbox.state.updated",
                "{\"sandbox\":{\"id\":\"sb-other\",\"state\":\"started\"},\"oldState\":\"starting\",\"newState\":\"started\"}");
        gateway.sendEvent("sandbox.state.updated",
                "{\"sandbox\":{\"id\":\"sb-1\",\"state\":\"started\"},\"oldState\":\"starting\",\"newState\":\"started\"}");

        JsonNode data = received.poll(WAIT_MILLIS, TimeUnit.MILLISECONDS);
        assertThat(data).isNotNull();
        assertThat(data.path("sandbox").path("id").asText()).isEqualTo("sb-1");
        assertThat(data.path("newState").asText()).isEqualTo("started");
        assertThat(received.poll(QUIET_MILLIS, TimeUnit.MILLISECONDS)).isNull();
    }

    private static void assertEmitted(FakeEventGateway.Emitted message, String event, Set<String> ids) {
        assertThat(message).as("expected %s %s", event, ids).isNotNull();
        assertThat(message.event).isEqualTo(event);
        // Exact multiset: a duplicated id on the wire must fail.
        assertThat(ids(message)).containsExactlyInAnyOrderElementsOf(ids);
    }

    private static List<String> ids(FakeEventGateway.Emitted message) {
        List<String> ids = new ArrayList<>();
        for (JsonNode id : message.payload.path("sandboxIds")) {
            ids.add(id.asText());
        }
        return ids;
    }

    private static void waitUntil(java.util.function.BooleanSupplier condition) throws InterruptedException {
        long deadline = System.currentTimeMillis() + WAIT_MILLIS;
        while (!condition.getAsBoolean()) {
            assertThat(System.currentTimeMillis()).as("condition not met in time").isLessThan(deadline);
            Thread.sleep(10);
        }
    }
}
