package io.github.mmhy2003.megooflags.internal.delivery;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.github.mmhy2003.megooflags.FakeServer;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.FakeServer.SseConnection;
import java.io.IOException;
import java.io.InputStream;
import java.io.SequenceInputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;

class SseReaderTest {
    private static final class Seen implements SseReader.Callbacks {
        int lines;
        final List<Long> retries = new ArrayList<>();
        final List<SseReader.Event> events = new ArrayList<>();

        @Override
        public void onLine() {
            lines++;
        }

        @Override
        public void onRetry(long millis) {
            retries.add(millis);
        }

        @Override
        public void onEvent(SseReader.Event event) {
            events.add(event);
        }
    }

    /** An input stream that hands out each chunk in its own read call. */
    private static InputStream chunks(byte[]... parts) {
        List<InputStream> streams = new ArrayList<>();
        for (byte[] p : parts) {
            streams.add(new java.io.ByteArrayInputStream(p));
        }
        return new SequenceInputStream(Collections.enumeration(streams));
    }

    private static byte[] utf8(String s) {
        return s.getBytes(StandardCharsets.UTF_8);
    }

    private static Seen read(byte[]... parts) throws IOException {
        Seen seen = new Seen();
        SseReader.read(chunks(parts), seen);
        return seen;
    }

    private static Seen read(String text) throws IOException {
        return read(utf8(text));
    }

    private static SseReader.Event ev(String name, String data, String id) {
        return new SseReader.Event(name, data, id);
    }

    static Stream<Arguments> cases() {
        return Stream.of(
                Arguments.of(
                        "named event with id",
                        "event: put\nid: 42\ndata: {\"flags\":{}}\n\n",
                        4,
                        List.of(ev("put", "{\"flags\":{}}", "42"))),
                Arguments.of("default name", "data: x\n\n", 2, List.of(ev("message", "x", ""))),
                Arguments.of(
                        "multi-line data", "data: a\ndata: b\ndata:c\n\n", 4, List.of(ev("message", "a\nb\nc", ""))),
                Arguments.of("CRLF endings", "event: put\r\nid: 7\r\ndata: x\r\n\r\n", 4, List.of(ev("put", "x", "7"))),
                Arguments.of(
                        "unknown event name passed through",
                        "event: mystery\ndata: 1\n\n",
                        3,
                        List.of(ev("mystery", "1", ""))),
                Arguments.of(
                        "unterminated last event discarded",
                        "data: a\n\nevent: put\ndata: b\n",
                        4,
                        List.of(ev("message", "a", ""))),
                Arguments.of(
                        "unterminated last line discarded", "data: a\n\ndata: b", 2, List.of(ev("message", "a", ""))),
                Arguments.of(
                        "state resets after dispatch",
                        "event: put\nid: 1\ndata: a\n\ndata: b\n\n",
                        6,
                        List.of(ev("put", "a", "1"), ev("message", "b", ""))),
                Arguments.of(
                        "empty line without data dispatches nothing",
                        "event: put\n\ndata: a\n\n",
                        4,
                        List.of(ev("message", "a", ""))),
                Arguments.of("id with NUL ignored", "id: a\0b\ndata: x\n\n", 3, List.of(ev("message", "x", ""))),
                Arguments.of(
                        "field without colon has an empty value",
                        "data\ndata\n\n",
                        3,
                        List.of(ev("message", "\n", ""))),
                Arguments.of(
                        "only one leading space removed, unknown field ignored",
                        "foo: bar\ndata:  two\n\n",
                        3,
                        List.of(ev("message", " two", ""))),
                Arguments.of("colon inside the value kept", "data: a: b\n\n", 2, List.of(ev("message", "a: b", ""))));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("cases")
    void parses(String name, String input, int lines, List<SseReader.Event> events) throws IOException {
        Seen seen = read(input);
        assertEquals(events, seen.events);
        assertEquals(lines, seen.lines);
        assertEquals(List.of(), seen.retries);
    }

    @Test
    void commentsCallOnLineOnly() throws IOException {
        Seen seen = read(": ping\n:\n\n");
        assertEquals(3, seen.lines);
        assertEquals(List.of(), seen.retries);
        assertEquals(List.of(), seen.events);
    }

    @Test
    void validRetryCallsOnRetryInMilliseconds() throws IOException {
        Seen seen = read("retry: 3000\n\n");
        assertEquals(2, seen.lines);
        assertEquals(List.of(3000L), seen.retries);
        assertEquals(List.of(), seen.events);
    }

    @ParameterizedTest
    @ValueSource(strings = {"retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n", "retry: ٣\n"})
    void invalidRetryIsIgnored(String input) throws IOException {
        assertEquals(List.of(), read(input).retries);
    }

    @Test
    void readsA1MiBDataLine() throws IOException {
        String big = "x".repeat(1 << 20);
        List<byte[]> parts = new ArrayList<>();
        parts.add(utf8("event: put\ndata: "));
        for (int i = 0; i < big.length(); i += 16_384) {
            parts.add(utf8(big.substring(i, i + 16_384)));
        }
        parts.add(utf8("\n\n"));
        Seen seen = read(parts.toArray(new byte[0][]));
        assertEquals(1, seen.events.size());
        assertEquals(big, seen.events.get(0).data());
    }

    @Test
    void decodesAMultiByteCharacterSplitAcrossTwoChunks() throws IOException {
        byte[] bytes = utf8("data: é€😀\n\n");
        int at = 0;
        while (bytes[at] != (byte) 0xe2) {
            at++;
        }
        at++;
        byte[] a = java.util.Arrays.copyOfRange(bytes, 0, at);
        byte[] b = java.util.Arrays.copyOfRange(bytes, at, bytes.length);
        assertEquals(List.of(ev("message", "é€😀", "")), read(a, b).events);
    }

    @Test
    void splitsLinesThatArriveInOneChunkOrByteByByte() throws IOException {
        String text = "event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n";
        Seen one = read(text);
        byte[] bytes = utf8(text);
        byte[][] single = new byte[bytes.length][];
        for (int i = 0; i < bytes.length; i++) {
            single[i] = new byte[] {bytes[i]};
        }
        Seen split = read(single);
        assertEquals(one.events, split.events);
        assertEquals(one.lines, split.lines);
        assertEquals(
                List.of("a", "b"),
                List.of(one.events.get(0).data(), one.events.get(1).data()));
    }

    @Test
    void anErrorThrownByOnEventStopsReading() {
        List<String> names = new ArrayList<>();
        IOException stop = new IOException("stop");
        IOException got = assertThrows(
                IOException.class,
                () -> SseReader.read(
                        chunks(utf8("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n")), new SseReader.Callbacks() {
                            @Override
                            public void onLine() {}

                            @Override
                            public void onRetry(long millis) {}

                            @Override
                            public void onEvent(SseReader.Event event) throws IOException {
                                names.add(event.name());
                                throw stop;
                            }
                        }));
        assertSame(stop, got);
        assertEquals(List.of("revoked"), names);
    }

    @Test
    void aReadErrorIsPassedThrough() {
        IOException boom = new IOException("boom");
        InputStream failing = new InputStream() {
            private final InputStream first = chunks(utf8(": ping\n"));

            @Override
            public int read() throws IOException {
                int b = first.read();
                if (b < 0) {
                    throw boom;
                }
                return b;
            }

            @Override
            public int read(byte[] buf, int off, int len) throws IOException {
                int n = first.read(buf, off, len);
                if (n < 0) {
                    throw boom;
                }
                return n;
            }
        };
        Seen seen = new Seen();
        assertSame(boom, assertThrows(IOException.class, () -> SseReader.read(failing, seen)));
        assertEquals(1, seen.lines);
    }

    @Test
    void readsAnHttpResponseBodyAsItArrives() throws Exception {
        try (FakeServer server = FakeServer.start()) {
            server.reply("/sdk/v1/stream", Reply.stream());
            HttpResponse<InputStream> res = HttpClient.newHttpClient()
                    .send(
                            HttpRequest.newBuilder(URI.create(server.url() + "/sdk/v1/stream"))
                                    .build(),
                            HttpResponse.BodyHandlers.ofInputStream());
            assertEquals(
                    "text/event-stream",
                    res.headers().firstValue("Content-Type").orElse(""));
            SseConnection conn = server.nextStream(Duration.ofSeconds(5));
            List<SseReader.Event> events = new ArrayList<>();
            conn.write(": ping\n\nevent: put\nid: 1\ndata: a\n\n");
            try (InputStream body = res.body()) {
                SseReader.read(body, new SseReader.Callbacks() {
                    @Override
                    public void onLine() {}

                    @Override
                    public void onRetry(long millis) {}

                    @Override
                    public void onEvent(SseReader.Event event) throws IOException {
                        events.add(event);
                        if (events.size() == 1) {
                            conn.write("event: put\nid: 2\ndata: b\n\n");
                            conn.end();
                        }
                    }
                });
            }
            assertEquals(List.of(ev("put", "a", "1"), ev("put", "b", "2")), events);
            assertEquals(1, server.received("/sdk/v1/stream").size());
        }
    }

    @Test
    void eventToString() {
        assertEquals("Event{name=put, id=1, data=a}", ev("put", "a", "1").toString());
    }
}
