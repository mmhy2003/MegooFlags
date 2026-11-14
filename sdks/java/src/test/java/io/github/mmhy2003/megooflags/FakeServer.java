package io.github.mmhy2003.megooflags;

import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

/**
 * A scripted HTTP server on 127.0.0.1 for the delivery, events and provider tests. Each path answers
 * from its queue of replies; the last reply of a queue repeats, and a path without replies gets
 * 404.
 */
public final class FakeServer implements AutoCloseable {
    /** A reply: a canned response, a held-open event stream, or a function of the request. */
    public abstract static class Reply {
        private Reply() {}

        /** A canned response with headers and a body. */
        public static Reply of(int status, Map<String, String> headers, String body) {
            return new Scripted(status, headers, body);
        }

        /** A canned response without headers. */
        public static Reply of(int status, String body) {
            return new Scripted(status, Map.of(), body);
        }

        /** An empty response. */
        public static Reply status(int status) {
            return new Scripted(status, Map.of(), "");
        }

        /** A text/event-stream response held open; the test writes to it. */
        public static Reply stream() {
            return new Stream(200, Map.of());
        }

        /** A response the function picks for each request; it may block to hold the reply back. */
        public static Reply respond(Function<Received, Reply> fn) {
            return new Responder(fn);
        }
    }

    private static final class Scripted extends Reply {
        final int status;
        final Map<String, String> headers;
        final String body;

        Scripted(int status, Map<String, String> headers, String body) {
            this.status = status;
            this.headers = headers;
            this.body = body;
        }
    }

    private static final class Stream extends Reply {
        final int status;
        final Map<String, String> headers;

        Stream(int status, Map<String, String> headers) {
            this.status = status;
            this.headers = headers;
        }
    }

    private static final class Responder extends Reply {
        final Function<Received, Reply> fn;

        Responder(Function<Received, Reply> fn) {
            this.fn = fn;
        }
    }

    /** A request the server received, with its whole body. */
    public static final class Received {
        public final String method;
        public final String path;
        /** The raw request URI: path and query. */
        public final String uri;

        public final Headers headers;
        public final String body;
        /** When the request arrived, in epoch milliseconds. */
        public final long at;

        Received(String method, String path, String uri, Headers headers, String body, long at) {
            this.method = method;
            this.path = path;
            this.uri = uri;
            this.headers = headers;
            this.body = body;
            this.at = at;
        }

        /** The first value of a header (case-insensitive), or null. */
        public String header(String name) {
            return headers.getFirst(name);
        }
    }

    /** An open event-stream response. */
    public static final class SseConnection {
        public final Received request;
        private final HttpExchange exchange;

        SseConnection(Received request, HttpExchange exchange) {
            this.request = request;
            this.exchange = exchange;
        }

        /** Sends text as is and flushes it. */
        public void write(String text) throws IOException {
            write(text.getBytes(StandardCharsets.UTF_8));
        }

        /** Sends bytes as is and flushes them. */
        public void write(byte[] bytes) throws IOException {
            OutputStream out = exchange.getResponseBody();
            out.write(bytes);
            out.flush();
        }

        /** Finishes the response cleanly. */
        public void end() {
            exchange.close();
        }
    }

    private final HttpServer server;
    private final ExecutorService executor;
    private final Map<String, Deque<Reply>> routes = new HashMap<>();
    private final List<Received> requests = Collections.synchronizedList(new ArrayList<>());
    private final List<SseConnection> streams = Collections.synchronizedList(new ArrayList<>());
    private final BlockingQueue<SseConnection> newStreams = new LinkedBlockingQueue<>();

    private FakeServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        executor = Executors.newCachedThreadPool(r -> {
            Thread t = new Thread(r, "fake-server");
            t.setDaemon(true);
            return t;
        });
        server.setExecutor(executor);
        server.createContext("/", this::handle);
    }

    /** Starts a server on 127.0.0.1 on a free port. */
    public static FakeServer start() throws IOException {
        FakeServer s = new FakeServer();
        s.server.start();
        return s;
    }

    /** The base URL, for example http://127.0.0.1:41234. */
    public String url() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    /** Adds replies to the queue of path. */
    public void reply(String path, Reply... replies) {
        synchronized (routes) {
            Deque<Reply> queue = routes.computeIfAbsent(path, p -> new ArrayDeque<>());
            Collections.addAll(queue, replies);
        }
    }

    /** Every request received so far. */
    public List<Received> requests() {
        synchronized (requests) {
            return new ArrayList<>(requests);
        }
    }

    /** The requests received for path. */
    public List<Received> received(String path) {
        List<Received> out = new ArrayList<>();
        for (Received r : requests()) {
            if (r.path.equals(path)) {
                out.add(r);
            }
        }
        return out;
    }

    /** The next event-stream connection not yet handed out, waiting up to timeout for it. */
    public SseConnection nextStream(Duration timeout) throws InterruptedException {
        SseConnection c = newStreams.poll(timeout.toMillis(), TimeUnit.MILLISECONDS);
        if (c == null) {
            throw new AssertionError("no event-stream connection within " + timeout);
        }
        return c;
    }

    /** Closes every open event stream. */
    public void closeStreams() {
        synchronized (streams) {
            for (SseConnection c : streams) {
                c.end();
            }
        }
    }

    /** Drops every connection and closes the listener. */
    public void stop() {
        closeStreams();
        server.stop(0);
        executor.shutdownNow();
    }

    @Override
    public void close() {
        stop();
    }

    private void handle(HttpExchange ex) throws IOException {
        long at = System.currentTimeMillis();
        String body;
        try (InputStream in = ex.getRequestBody()) {
            body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
        Received req = new Received(
                ex.getRequestMethod(),
                ex.getRequestURI().getPath(),
                ex.getRequestURI().toString(),
                copy(ex.getRequestHeaders()),
                body,
                at);
        requests.add(req);
        Reply reply;
        synchronized (routes) {
            Deque<Reply> queue = routes.get(req.path);
            if (queue == null || queue.isEmpty()) {
                reply = null;
            } else {
                reply = queue.size() > 1 ? queue.poll() : queue.peek();
            }
        }
        answer(req, ex, reply);
    }

    private void answer(Received req, HttpExchange ex, Reply reply) throws IOException {
        if (reply instanceof Responder) {
            reply = ((Responder) reply).fn.apply(req);
        }
        if (reply == null) {
            ex.sendResponseHeaders(404, -1);
            ex.close();
            return;
        }
        if (reply instanceof Stream) {
            Stream s = (Stream) reply;
            ex.getResponseHeaders().set("Content-Type", "text/event-stream");
            ex.getResponseHeaders().set("Cache-Control", "no-cache");
            s.headers.forEach((k, v) -> ex.getResponseHeaders().set(k, v));
            ex.sendResponseHeaders(s.status, 0);
            ex.getResponseBody().flush();
            SseConnection c = new SseConnection(req, ex);
            streams.add(c);
            newStreams.add(c);
            return;
        }
        Scripted s = (Scripted) reply;
        s.headers.forEach((k, v) -> ex.getResponseHeaders().set(k, v));
        byte[] bytes = s.body.getBytes(StandardCharsets.UTF_8);
        boolean noBody = bytes.length == 0 || s.status == 204 || s.status == 304;
        ex.sendResponseHeaders(s.status, noBody ? -1 : bytes.length);
        if (!noBody) {
            try (OutputStream out = ex.getResponseBody()) {
                out.write(bytes);
            }
        }
        ex.close();
    }

    private static Headers copy(Headers in) {
        Map<String, List<String>> m = new LinkedHashMap<>();
        in.forEach((k, v) -> m.put(k, new ArrayList<>(v)));
        Headers out = new Headers();
        out.putAll(m);
        return out;
    }
}
