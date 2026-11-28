package io.github.mmhy2003.megooflags.internal.delivery;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import io.github.mmhy2003.megooflags.internal.json.Json;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class CacheTest {
    private static final String KEY = "mf_server_secret-key-123";
    private static final String RAW = "{\"project\":\"p\",\"environment\":\"e\",\"flags\":{},\"segments\":{}}";
    private static final boolean POSIX =
            FileSystems.getDefault().supportedFileAttributeViews().contains("posix");

    @TempDir
    Path dir;

    private static String fileName(String key) throws Exception {
        byte[] sum = MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8));
        StringBuilder hex = new StringBuilder();
        for (byte b : sum) {
            hex.append(String.format("%02x", b));
        }
        return "megooflags-" + hex.substring(0, 16) + ".json";
    }

    private List<String> entries() throws IOException {
        try (Stream<Path> s = Files.list(dir)) {
            return s.map(p -> p.getFileName().toString()).sorted().collect(Collectors.toList());
        }
    }

    @Test
    void roundTripsTheRulesetAndTheEtag() throws Exception {
        Cache c = new Cache(dir, KEY);
        c.save(RAW, "\"v1\"");
        Cache.Entry e = c.load();
        assertEquals(Json.parse(RAW), e.ruleset());
        assertEquals("\"v1\"", e.etag());
    }

    @Test
    void writesEtagAndRulesetWithTheRulesetEmbeddedAsReceived() throws Exception {
        new Cache(dir, KEY).save(RAW, "\"v1\"");
        String text = Files.readString(dir.resolve(fileName(KEY)), StandardCharsets.UTF_8);
        assertTrue(text.contains("\"ruleset\":" + RAW), text);
        assertEquals(Map.of("etag", "\"v1\"", "ruleset", Json.parse(RAW)), Json.parse(text.trim()));
    }

    @Test
    void readsAFileWrittenByTheGoSdk() throws Exception {
        Files.writeString(
                dir.resolve(fileName(KEY)),
                "{\"etag\":\"\\\"g\\\"\",\"ruleset\":" + RAW + "}\n",
                StandardCharsets.UTF_8);
        Cache.Entry e = new Cache(dir, KEY).load();
        assertEquals(Json.parse(RAW), e.ruleset());
        assertEquals("\"g\"", e.etag());
    }

    @Test
    void namesTheFileAfter16HexCharactersOfTheKeyHash() throws Exception {
        new Cache(dir, KEY).save("{}", "x");
        List<String> names = entries();
        assertEquals(List.of(fileName(KEY)), names);
        assertTrue(names.get(0).matches("megooflags-[0-9a-f]{16}\\.json"));
        assertFalse(names.get(0).contains(KEY));
        assertFalse(Files.readString(dir.resolve(names.get(0))).contains(KEY));
        assertNotEquals(fileName("another-key"), fileName(KEY));
    }

    @Test
    void missingFileIsNotFound() throws Exception {
        assertNull(new Cache(dir, KEY).load());
    }

    @Test
    void corruptFileIsAnError() throws Exception {
        Path p = dir.resolve(fileName(KEY));
        for (String bad : List.of("{not json", "[]", "{\"etag\":1,\"ruleset\":{}}", "{\"etag\":\"x\"}", "")) {
            Files.writeString(p, bad);
            IOException e = assertThrows(IOException.class, () -> new Cache(dir, KEY).load(), bad);
            assertTrue(e.getMessage().startsWith("megooflags: corrupt cache file "), e.getMessage());
            assertFalse(e.getMessage().contains(KEY));
        }
    }

    @Test
    void missingEtagIsEmpty() throws Exception {
        Files.writeString(dir.resolve(fileName(KEY)), "{\"ruleset\":{}}");
        assertEquals("", new Cache(dir, KEY).load().etag());
    }

    @Test
    void unreadableFileIsAnError() throws Exception {
        Files.write(dir.resolve(fileName(KEY)), new byte[] {'{', '"', (byte) 0xff, '"', '}'});
        assertThrows(IOException.class, () -> new Cache(dir, KEY).load());
    }

    @Test
    void fileModeIs0600() throws Exception {
        assumeTrue(POSIX);
        new Cache(dir, KEY).save(RAW, "e");
        assertEquals(
                PosixFilePermissions.fromString("rw-------"),
                Files.getPosixFilePermissions(dir.resolve(fileName(KEY))));
    }

    @Test
    void atomicReplaceLeavesNoTempFile() throws Exception {
        Cache c = new Cache(dir, KEY);
        c.save(RAW, "a");
        c.save("{\"flags\":{}}", "b");
        assertEquals(List.of(fileName(KEY)), entries());
        assertEquals("b", c.load().etag());
        assertEquals(Json.parse("{\"flags\":{}}"), c.load().ruleset());
    }

    @Test
    void invalidRawIsRefusedAndWritesNothing() throws Exception {
        Cache c = new Cache(dir, KEY);
        assertThrows(IOException.class, () -> c.save("{oops", "a"));
        assertEquals(List.of(), entries());
    }

    @Test
    void createsTheDirectory() throws Exception {
        Path nested = dir.resolve("a").resolve("b");
        new Cache(nested, KEY).save(RAW, "e");
        assertTrue(Files.isRegularFile(nested.resolve(fileName(KEY))));
        if (POSIX) {
            assertEquals(PosixFilePermissions.fromString("rwx------"), Files.getPosixFilePermissions(nested));
        }
    }

    @Test
    void noCacheDirectoryLoadsNothingAndSavesNothing() throws Exception {
        Cache c = new Cache(null, KEY);
        assertNull(c.load());
        c.save(RAW, "e");
        assertNull(c.load());
        assertEquals(List.of(), entries());
    }
}
