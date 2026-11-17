// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.internal;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okhttp3.mockwebserver.Dispatcher;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Minimal Socket.IO (Engine.IO v4, websocket transport) server for tests. Records the
 * handshake requests and every event the client emits.
 */
public final class FakeEventGateway implements AutoCloseable {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String ENGINE_OPEN =
            "0{\"sid\":\"engine\",\"upgrades\":[],\"pingInterval\":25000,\"pingTimeout\":20000,\"maxPayload\":1000000}";

    /** Event emitted by the client: name plus first argument. */
    public static final class Emitted {
        public final String event;
        public final JsonNode payload;

        Emitted(String event, JsonNode payload) {
            this.event = event;
            this.payload = payload;
        }
    }

    private final MockWebServer server = new MockWebServer();
    private final BlockingQueue<Emitted> emitted = new LinkedBlockingQueue<>();
    private final BlockingQueue<RecordedRequest> handshakes = new LinkedBlockingQueue<>();
    private final AtomicReference<WebSocket> current = new AtomicReference<>();
    private final List<WebSocket> opened = new java.util.concurrent.CopyOnWriteArrayList<>();
    private final Object acceptLock = new Object();
    private final List<WebSocket> pendingAccept = new ArrayList<>();
    private boolean autoAccept = true;

    public FakeEventGateway() throws IOException {
        server.setDispatcher(new HandshakeDispatcher());
        server.start();
    }

    public String apiUrl() {
        return server.url("/api").toString();
    }

    public int requestCount() {
        return server.getRequestCount();
    }

    public RecordedRequest takeHandshake(long timeoutMillis) throws InterruptedException {
        return handshakes.poll(timeoutMillis, TimeUnit.MILLISECONDS);
    }

    public Emitted takeEmitted(long timeoutMillis) throws InterruptedException {
        return emitted.poll(timeoutMillis, TimeUnit.MILLISECONDS);
    }

    /** Holds Socket.IO namespace connects until {@link #resumeAccepting()}. */
    public void holdConnects() {
        synchronized (acceptLock) {
            autoAccept = false;
        }
    }

    /** Accepts held namespace connects and every later one. */
    public void resumeAccepting() {
        synchronized (acceptLock) {
            autoAccept = true;
            for (WebSocket ws : pendingAccept) {
                accept(ws);
            }
            pendingAccept.clear();
        }
    }

    /**
     * Breaks the current connection with an invalid Engine.IO packet, which the client
     * treats as a transport error and reconnects on its own.
     */
    public void dropConnection() {
        WebSocket ws = current.getAndSet(null);
        if (ws != null) {
            ws.send("9");
        }
    }

    public void sendEvent(String event, String jsonPayload) {
        WebSocket ws = current.get();
        if (ws == null) {
            throw new IllegalStateException("no connection");
        }
        ws.send("42[\"" + event + "\"," + jsonPayload + "]");
    }

    private static void accept(WebSocket ws) {
        ws.send("40{\"sid\":\"ns\"}");
    }

    @Override
    public void close() throws IOException {
        for (WebSocket ws : opened) {
            ws.close(1000, "shutdown");
        }
        server.shutdown();
    }

    private final class HandshakeDispatcher extends Dispatcher {
        @Override
        public MockResponse dispatch(RecordedRequest request) {
            handshakes.add(request);
            return new MockResponse().withWebSocketUpgrade(new Listener());
        }
    }

    private final class Listener extends WebSocketListener {
        @Override
        public void onOpen(WebSocket webSocket, Response response) {
            current.set(webSocket);
            opened.add(webSocket);
            webSocket.send(ENGINE_OPEN);
        }

        @Override
        public void onMessage(WebSocket webSocket, String text) {
            if (text.startsWith("40")) {
                synchronized (acceptLock) {
                    if (autoAccept) {
                        accept(webSocket);
                    } else {
                        pendingAccept.add(webSocket);
                    }
                }
            } else if (text.startsWith("42")) {
                try {
                    JsonNode packet = MAPPER.readTree(text.substring(2));
                    emitted.add(new Emitted(packet.get(0).asText(), packet.get(1)));
                } catch (IOException e) {
                    throw new IllegalStateException(e);
                }
            }
        }
    }
}
