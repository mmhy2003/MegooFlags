package io.github.mmhy2003.megooflags.internal.delivery;

import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;

/**
 * The request of one worker (the stream or the poller), which another thread can close at any
 * point without leaking the connection. The worker never gets interrupted: on JDK 11 an
 * interrupted send() abandons the exchange, and the connection stays open when its response
 * arrives. Instead the worker waits on a copy of the response future, close() cancels the copy,
 * and an abandoned response has its body closed when it arrives.
 */
final class Exchange {
    private final Object lock = new Object();
    private boolean closed;
    private CompletableFuture<HttpResponse<InputStream>> waiting;
    private InputStream body;

    /**
     * Sends req and returns the response once its headers arrived; its body stays open until
     * release(). Throws the client's IOException, or an IOException once closed.
     */
    HttpResponse<InputStream> send(HttpClient client, HttpRequest req) throws IOException, InterruptedException {
        CompletableFuture<HttpResponse<InputStream>> sent;
        CompletableFuture<HttpResponse<InputStream>> wait;
        synchronized (lock) {
            if (closed) {
                throw closedError();
            }
            sent = client.sendAsync(req, HttpResponse.BodyHandlers.ofInputStream());
            wait = sent.copy();
            waiting = wait;
        }
        HttpResponse<InputStream> res;
        try {
            res = wait.get();
        } catch (CancellationException e) {
            abandon(sent);
            throw closedError();
        } catch (InterruptedException e) {
            abandon(sent);
            throw e;
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            throw cause instanceof IOException ? (IOException) cause : new IOException(cause);
        } finally {
            synchronized (lock) {
                waiting = null;
            }
        }
        synchronized (lock) {
            if (!closed) {
                body = res.body();
                return res;
            }
        }
        closeQuietly(res.body());
        throw closedError();
    }

    /** Closes the body of the last response. */
    void release() {
        InputStream in;
        synchronized (lock) {
            in = body;
            body = null;
        }
        closeQuietly(in);
    }

    /** Ends a send or a body read in progress, and makes every later send fail. Idempotent. */
    void close() {
        CompletableFuture<HttpResponse<InputStream>> wait;
        InputStream in;
        synchronized (lock) {
            closed = true;
            wait = waiting;
            in = body;
        }
        if (wait != null) {
            wait.cancel(false);
        }
        closeQuietly(in);
    }

    /** The response may still come: close its body then, which releases the connection. */
    private static void abandon(CompletableFuture<HttpResponse<InputStream>> sent) {
        sent.thenAccept(res -> closeQuietly(res.body()));
    }

    private static IOException closedError() {
        return new IOException("megooflags: request closed");
    }

    static void closeQuietly(InputStream in) {
        if (in == null) {
            return;
        }
        try {
            in.close();
        } catch (IOException e) {
            // Nothing left to release.
        }
    }
}
