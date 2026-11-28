package io.github.mmhy2003.megooflags.internal.delivery;

import io.github.mmhy2003.megooflags.internal.json.Json;
import io.github.mmhy2003.megooflags.internal.json.JsonException;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import java.util.Set;

/**
 * The last-known ruleset on disk: the file {@code <dir>/megooflags-<first 16 hex of
 * sha256(key)>.json} holding {@code {"etag", "ruleset"}}, the Go SDK's format, so a cache directory
 * can be shared between SDKs. The key itself is never written to disk. Without a directory, load
 * finds nothing and save does nothing.
 */
public final class Cache {
    /** A cached ruleset: the decoded ruleset tree and its ETag. */
    public static final class Entry {
        private final Object ruleset;
        private final String etag;

        Entry(Object ruleset, String etag) {
            this.ruleset = ruleset;
            this.etag = etag;
        }

        /** The ruleset as a JSON tree (see {@link Json#parse}). */
        public Object ruleset() {
            return ruleset;
        }

        public String etag() {
            return etag;
        }
    }

    private static final Set<PosixFilePermission> DIR_MODE = PosixFilePermissions.fromString("rwx------");
    private static final Set<PosixFilePermission> FILE_MODE = PosixFilePermissions.fromString("rw-------");

    private final Path dir;
    private final Path path;

    /** A cache in dir for sdkKey; dir may be null for no cache. */
    public Cache(Path dir, String sdkKey) {
        this.dir = dir;
        this.path = dir == null ? null : dir.resolve(fileName(sdkKey));
    }

    /**
     * Returns the cached ruleset and ETag, or null when there is no file. Throws for a file it
     * cannot read or that is corrupt.
     */
    public Entry load() throws IOException {
        if (path == null) {
            return null;
        }
        String text;
        try {
            text = Files.readString(path, StandardCharsets.UTF_8);
        } catch (NoSuchFileException e) {
            return null;
        }
        Object file;
        try {
            file = Json.parse(text);
        } catch (JsonException e) {
            throw corrupt(e.getMessage());
        }
        if (!(file instanceof Map)) {
            throw corrupt("not a JSON object");
        }
        Map<?, ?> m = (Map<?, ?>) file;
        Object etag = m.containsKey("etag") ? m.get("etag") : "";
        if (!(etag instanceof String)) {
            throw corrupt("etag is not a string");
        }
        if (!m.containsKey("ruleset")) {
            throw corrupt("no ruleset");
        }
        return new Entry(m.get("ruleset"), (String) etag);
    }

    /**
     * Writes the ruleset JSON text, embedded as received, and its ETag to a temp file in the cache
     * directory and moves it over the cache file, so a reader never sees a partial write. Throws
     * when raw is not JSON or the write fails, and leaves no temp file behind.
     */
    public void save(String raw, String etag) throws IOException {
        if (path == null) {
            return;
        }
        try {
            Json.parse(raw);
        } catch (JsonException e) {
            throw new IOException("megooflags: cannot cache an invalid ruleset: " + e.getMessage(), e);
        }
        byte[] content =
                ("{\"etag\":" + Json.write(etag) + ",\"ruleset\":" + raw + "}\n").getBytes(StandardCharsets.UTF_8);
        boolean posix = FileSystems.getDefault().supportedFileAttributeViews().contains("posix");
        if (posix) {
            Files.createDirectories(dir, PosixFilePermissions.asFileAttribute(DIR_MODE));
        } else {
            Files.createDirectories(dir);
        }
        FileAttribute<?>[] attrs = posix
                ? new FileAttribute<?>[] {PosixFilePermissions.asFileAttribute(FILE_MODE)}
                : new FileAttribute<?>[0];
        Path tmp = Files.createTempFile(dir, ".megooflags-", ".tmp", attrs);
        try {
            try (FileChannel ch = FileChannel.open(tmp, StandardOpenOption.WRITE)) {
                ByteBuffer buf = ByteBuffer.wrap(content);
                while (buf.hasRemaining()) {
                    ch.write(buf);
                }
                ch.force(true);
            }
            if (posix) {
                Files.setPosixFilePermissions(tmp, FILE_MODE);
            }
            Files.move(tmp, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException | RuntimeException e) {
            Files.deleteIfExists(tmp);
            throw e;
        }
    }

    private IOException corrupt(String why) {
        return new IOException("megooflags: corrupt cache file " + path + ": " + why);
    }

    private static String fileName(String sdkKey) {
        byte[] sum;
        try {
            sum = MessageDigest.getInstance("SHA-256").digest(sdkKey.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is not available", e);
        }
        StringBuilder hex = new StringBuilder("megooflags-");
        for (int i = 0; i < 8; i++) {
            hex.append(Character.forDigit((sum[i] >> 4) & 0xf, 16)).append(Character.forDigit(sum[i] & 0xf, 16));
        }
        return hex.append(".json").toString();
    }
}
