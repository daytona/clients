// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.toolbox.client.model.ProcessLogChannel;
import io.daytona.toolbox.client.model.ProcessLogFrame;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CharsetDecoder;
import java.nio.charset.CoderResult;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.function.Consumer;

final class ProcessOutputCollector {
    private final ChannelDecoder stdout;
    private final ChannelDecoder stderr;

    ProcessOutputCollector(Consumer<String> onStdout, Consumer<String> onStderr) {
        stdout = new ChannelDecoder(onStdout);
        stderr = new ChannelDecoder(onStderr);
    }

    void accept(ProcessLogFrame frame) {
        if (frame == null || frame.getData() == null) return;
        byte[] bytes = "base64".equals(frame.getEncoding())
                ? Base64.getDecoder().decode(frame.getData())
                : frame.getData().getBytes(StandardCharsets.UTF_8);
        ProcessLogChannel channel = frame.getChannel();
        if (channel == ProcessLogChannel.ChannelStdout || channel == ProcessLogChannel.ChannelPty) {
            stdout.accept(bytes);
        } else if (channel == ProcessLogChannel.ChannelStderr) {
            stderr.accept(bytes);
        }
    }

    Collected finish() {
        stdout.finish();
        stderr.finish();
        return new Collected(stdout.text(), stderr.text());
    }

    static final class Collected {
        final String stdout;
        final String stderr;

        Collected(String stdout, String stderr) {
            this.stdout = stdout;
            this.stderr = stderr;
        }
    }

    private static final class ChannelDecoder {
        private final CharsetDecoder decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPLACE)
                .onUnmappableCharacter(CodingErrorAction.REPLACE);
        private final ByteArrayOutputStream pending = new ByteArrayOutputStream();
        private final StringBuilder output = new StringBuilder();
        private final Consumer<String> callback;

        private ChannelDecoder(Consumer<String> callback) {
            this.callback = callback;
        }

        private void accept(byte[] bytes) {
            pending.write(bytes, 0, bytes.length);
            decode(false);
        }

        private void finish() {
            decode(true);
            CharBuffer chars = CharBuffer.allocate(8);
            while (true) {
                CoderResult result = decoder.flush(chars);
                emit(chars);
                if (result.isUnderflow()) return;
                if (!result.isOverflow()) throw codingFailure(result);
            }
        }

        private void decode(boolean endOfInput) {
            ByteBuffer input = ByteBuffer.wrap(pending.toByteArray());
            CharBuffer chars = CharBuffer.allocate(Math.max(8, input.remaining() * 2 + 2));
            while (true) {
                CoderResult result = decoder.decode(input, chars, endOfInput);
                emit(chars);
                if (result.isUnderflow()) break;
                if (!result.isOverflow()) throw codingFailure(result);
            }
            byte[] remainder = new byte[input.remaining()];
            input.get(remainder);
            pending.reset();
            pending.write(remainder, 0, remainder.length);
        }

        private void emit(CharBuffer chars) {
            chars.flip();
            if (!chars.hasRemaining()) {
                chars.clear();
                return;
            }
            String value = chars.toString();
            output.append(value);
            if (callback != null) callback.accept(value);
            chars.clear();
        }

        private String text() { return output.toString(); }

        private RuntimeException codingFailure(CoderResult result) {
            try {
                result.throwException();
                return new IllegalStateException("Unexpected UTF-8 decoder state");
            } catch (CharacterCodingException e) {
                return new IllegalStateException("Failed to decode process output", e);
            }
        }
    }
}
