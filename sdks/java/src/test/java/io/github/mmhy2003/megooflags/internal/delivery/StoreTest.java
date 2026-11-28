package io.github.mmhy2003.megooflags.internal.delivery;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;

import io.github.mmhy2003.megooflags.internal.engine.Ruleset;
import io.github.mmhy2003.megooflags.internal.engine.RulesetParser;
import org.junit.jupiter.api.Test;

class StoreTest {
    private static Ruleset ruleset() throws Exception {
        return RulesetParser.parse(
                "{\"schema_version\":1,\"project\":\"p\",\"environment\":\"e\",\"flags\":{},\"segments\":{}}");
    }

    @Test
    void loadBeforeSwapIsNull() {
        assertNull(new Store().load());
    }

    @Test
    void swapReturnsTheOldSnapshot() throws Exception {
        Store s = new Store();
        Snapshot a = new Snapshot(ruleset(), "\"a\"", Snapshot.Source.BOOTSTRAP);
        Snapshot b = new Snapshot(ruleset(), "\"b\"", Snapshot.Source.STREAM);
        assertNull(s.swap(a));
        assertSame(a, s.load());
        assertSame(a, s.swap(b));
        assertSame(b, s.load());
        assertEquals("\"b\"", s.load().etag());
        assertEquals(Snapshot.Source.STREAM, s.load().source());
        assertSame(b.ruleset(), s.load().ruleset());
    }

    @Test
    void sourceNamesMatchTheBlueprint() {
        assertEquals("bootstrap", Snapshot.Source.BOOTSTRAP.toString());
        assertEquals("cache", Snapshot.Source.CACHE.toString());
        assertEquals("stream", Snapshot.Source.STREAM.toString());
        assertEquals("poll", Snapshot.Source.POLL.toString());
    }
}
