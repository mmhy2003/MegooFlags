package io.github.mmhy2003.megooflags.internal.engine;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class SemverTest {
    @ParameterizedTest
    @ValueSource(
            strings = {
                "1.2.3",
                "1.0.0-alpha.1",
                "1.0.0+build.5",
                "1.0.0-0.3.7",
                "1.0.0-x-y-z.--",
                "1.0.0-alpha+b.1",
                "0.0.0"
            })
    void valid(String s) {
        assertNotNull(Semver.parse(s));
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "v1.2.3",
                "1.2",
                "1.2.3.4",
                "01.2.3",
                "1.2.3-01",
                "1.2.3-",
                "1.2.3+",
                "1.2.3-a..b",
                "1.2.3\n",
                " 1.2.3",
                "1.٢.3",
                "1.2.3-α",
                "1.2.3+a..b",
                ""
            })
    void invalid(String s) {
        assertNull(Semver.parse(s));
    }

    @Test
    void precedence() {
        String[] ordered = {
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0"
        };
        for (int i = 0; i < ordered.length; i++) {
            for (int j = 0; j < ordered.length; j++) {
                assertEquals(
                        Integer.compare(i, j),
                        Semver.compare(Semver.parse(ordered[i]), Semver.parse(ordered[j])),
                        ordered[i] + " vs " + ordered[j]);
            }
        }
    }

    @Test
    void buildIsIgnored() {
        assertEquals(0, Semver.compare(Semver.parse("1.0.0+b1"), Semver.parse("1.0.0+b2")));
    }

    @Test
    void coreComparesWithoutOverflow() {
        assertEquals(1, Semver.compare(Semver.parse("99999999999999999999.0.0"), Semver.parse("9.0.0")));
        assertEquals(-1, Semver.compare(Semver.parse("1.9.0"), Semver.parse("1.10.0")));
    }
}
