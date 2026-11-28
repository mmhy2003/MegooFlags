package io.github.mmhy2003.megooflags.internal.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class DatesTest {
    @Test
    void parses() {
        assertEquals(0L, Dates.parseRfc3339Millis("1970-01-01T00:00:00Z"));
        assertEquals(123L, Dates.parseRfc3339Millis("1970-01-01T00:00:00.123456789Z"));
        assertEquals(500L, Dates.parseRfc3339Millis("1970-01-01T00:00:00.5Z"));
        assertEquals(0L, Dates.parseRfc3339Millis("1970-01-01T01:00:00+01:00"));
        assertEquals(-1000L, Dates.parseRfc3339Millis("1969-12-31T23:59:59Z"));
        assertEquals(1767225600000L, Dates.parseRfc3339Millis("2026-01-01t00:00:00z"));
        assertEquals(1767225600000L, Dates.parseRfc3339Millis("2026-01-01 00:00:00Z"));
        assertEquals(1767225600000L, Dates.parseRfc3339Millis("2025-12-31T19:00:00-05:00"));
        assertNotNull(Dates.parseRfc3339Millis("2024-02-29T00:00:00Z"));
        assertNotNull(Dates.parseRfc3339Millis("2026-01-01T00:00:00+23:59"));
        assertEquals(-62135596800000L, Dates.parseRfc3339Millis("0001-01-01T00:00:00Z"));
        assertEquals(253402300799999L, Dates.parseRfc3339Millis("9999-12-31T23:59:59.999Z"));
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "2026-01-01",
                "2026-02-30T00:00:00Z",
                "2025-02-29T00:00:00Z",
                "1900-02-29T00:00:00Z",
                "2026-04-31T00:00:00Z",
                "2026-13-01T00:00:00Z",
                "2026-00-01T00:00:00Z",
                "2026-01-00T00:00:00Z",
                "2026-01-01T24:00:00Z",
                "2026-01-01T00:60:00Z",
                "2026-01-01T00:00:60Z",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00+24:00",
                "2026-01-01T00:00:00+0100",
                "0000-01-01T00:00:00Z",
                "2026-01-01T00:00:00.Z",
                "2026-01-01T00:00:00Z\n",
                " 2026-01-01T00:00:00Z",
                "2026-01-01X00:00:00Z",
                "٢026-01-01T00:00:00Z",
                "2026-01-01T00:00:00.١Z",
                "26-01-01T00:00:00Z",
                ""
            })
    void invalid(String s) {
        assertNull(Dates.parseRfc3339Millis(s));
    }

    @Test
    void toMillis() {
        assertEquals(1500L, Dates.toMillis(1500.9));
        assertEquals(-1L, Dates.toMillis(-0.5));
        assertEquals(1500L, Dates.toMillis(1500L));
        assertEquals(0L, Dates.toMillis("1970-01-01T00:00:00Z"));
        assertNull(Dates.toMillis("nope"));
        assertNull(Dates.toMillis(Double.NaN));
        assertNull(Dates.toMillis(Double.POSITIVE_INFINITY));
        assertNull(Dates.toMillis(Double.NEGATIVE_INFINITY));
        assertNull(Dates.toMillis(true));
        assertNull(Dates.toMillis(null));
        assertNull(Dates.toMillis(List.of()));
    }
}
