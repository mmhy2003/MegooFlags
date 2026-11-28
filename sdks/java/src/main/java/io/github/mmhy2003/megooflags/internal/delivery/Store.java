package io.github.mmhy2003.megooflags.internal.delivery;

import java.util.concurrent.atomic.AtomicReference;

/** Holds the current snapshot. Readers never lock. */
public final class Store {
    private final AtomicReference<Snapshot> current = new AtomicReference<>();

    /** The current snapshot, or null before the first swap. */
    public Snapshot load() {
        return current.get();
    }

    /** Installs next and returns the snapshot it replaced (null the first time). */
    public Snapshot swap(Snapshot next) {
        return current.getAndSet(next);
    }
}
