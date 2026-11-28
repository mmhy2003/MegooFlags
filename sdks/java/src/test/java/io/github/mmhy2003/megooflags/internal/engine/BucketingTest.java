package io.github.mmhy2003.megooflags.internal.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.util.List;
import org.junit.jupiter.api.Test;

class BucketingTest {
    @Test
    void bucketText() {
        assertEquals("u", Bucketing.bucketText("u"));
        assertEquals("42", Bucketing.bucketText(42L));
        assertEquals("42", Bucketing.bucketText(42.0));
        assertEquals("0", Bucketing.bucketText(-0.0));
        assertEquals("-9007199254740991", Bucketing.bucketText(-9007199254740991L));
        assertEquals("9007199254740991", Bucketing.bucketText(9007199254740991.0));
        assertNull(Bucketing.bucketText(42.5));
        assertNull(Bucketing.bucketText(true));
        assertNull(Bucketing.bucketText(null));
        assertNull(Bucketing.bucketText(List.of(1L)));
        assertNull(Bucketing.bucketText(9007199254740992L));
        assertNull(Bucketing.bucketText(9007199254740992.0));
        assertNull(Bucketing.bucketText(Double.NaN));
        assertNull(Bucketing.bucketText(Double.POSITIVE_INFINITY));
    }

    @Test
    void bucket() {
        assertEquals(68258, Bucketing.bucket("f", "u"));
    }

    @Test
    void pickVariation() {
        List<Bucketing.Weight> weights = List.of(
                new Bucketing.Weight("a", 30000), new Bucketing.Weight("b", 40000), new Bucketing.Weight("c", 30000));
        assertEquals("a", Bucketing.pickVariation(weights, 29999));
        assertEquals("b", Bucketing.pickVariation(weights, 30000));
        assertEquals("b", Bucketing.pickVariation(weights, 68258));
        assertEquals("c", Bucketing.pickVariation(weights, 99999));
        assertEquals(
                "b",
                Bucketing.pickVariation(List.of(new Bucketing.Weight("a", 0), new Bucketing.Weight("b", 100000)), 0));
    }
}
