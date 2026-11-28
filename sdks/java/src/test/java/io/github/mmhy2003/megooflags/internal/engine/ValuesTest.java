package io.github.mmhy2003.megooflags.internal.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ValuesTest {
    @Test
    void isNumber() {
        assertTrue(Values.isNumber(1L));
        assertTrue(Values.isNumber(1.5));
        assertTrue(Values.isNumber(3));
        assertFalse(Values.isNumber(true));
        assertFalse(Values.isNumber("1"));
        assertFalse(Values.isNumber(null));
    }

    @Test
    void isScalar() {
        assertTrue(Values.isScalar("a"));
        assertTrue(Values.isScalar(false));
        assertTrue(Values.isScalar(2L));
        assertTrue(Values.isScalar(2.5));
        assertFalse(Values.isScalar(null));
        assertFalse(Values.isScalar(List.of()));
        assertFalse(Values.isScalar(Map.of()));
    }

    @Test
    void jsonEqual() {
        assertTrue(Values.jsonEqual(1L, 1.0));
        assertFalse(Values.jsonEqual(true, 1L));
        assertFalse(Values.jsonEqual(1L, true));
        assertFalse(Values.jsonEqual("1", 1L));
        assertFalse(Values.jsonEqual(null, null));
        assertTrue(Values.jsonEqual(false, false));
        assertFalse(Values.jsonEqual(false, true));
        assertTrue(Values.jsonEqual("a", "a"));
        assertFalse(Values.jsonEqual(List.of(), List.of()));
    }

    @Test
    void integersCompareExactly() {
        // 2^53 and 2^53+1 are the same double but different integers.
        assertFalse(Values.jsonEqual(9007199254740992L, 9007199254740993L));
        assertEquals(-1, Values.compareNumbers(9007199254740992L, 9007199254740993L));
        assertEquals(1, Values.compareNumbers(Long.MAX_VALUE, Long.MAX_VALUE - 1));
        assertEquals(0, Values.compareNumbers(3L, 3));
    }

    @Test
    void mixedNumbersCompareAsDoubles() {
        assertTrue(Values.jsonEqual(9007199254740993L, 9007199254740992.0));
        assertEquals(0, Values.compareNumbers(2L, 2.0));
        assertEquals(-1, Values.compareNumbers(2L, 2.5));
        assertEquals(1, Values.compareNumbers(-0.5, -1L));
        assertEquals(0, Values.compareNumbers(0.0, -0.0));
    }

    @Test
    void nanEqualsNothing() {
        assertFalse(Values.jsonEqual(Double.NaN, 5L));
        assertFalse(Values.jsonEqual(5L, Double.NaN));
        assertFalse(Values.jsonEqual(Double.NaN, 5.0));
        assertFalse(Values.jsonEqual(Double.NaN, Double.NaN));
        assertFalse(Values.jsonEqual(Float.NaN, 0L));
        assertTrue(Values.jsonEqual(Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY));
    }
}
