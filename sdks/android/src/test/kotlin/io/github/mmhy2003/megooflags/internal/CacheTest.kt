package io.github.mmhy2003.megooflags.internal

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

private const val KEY = "mf_client_secret123"

// 2026-11-14T12:00:00Z.
private const val SAVED_AT = 1_794_657_600_000L

private val results =
    listOf(
        EvaluateResult("banner", "blue", "blue", "TARGETING_MATCH", null, null),
        EvaluateResult("gone", false, null, "ERROR", "FLAG_NOT_FOUND", "no such flag"),
    )

private fun stored(
    etag: String? = "\"e1\"",
    hash: String = "h1",
): StoredResults = StoredResults(results, etag, Source.NETWORK, hash, "user-1")

class CacheTest {
    private lateinit var dir: File
    private val logger = LineLogger()

    @Before
    fun setUp() {
        dir = Files.createTempDirectory("megooflags-tests-").toFile()
    }

    @After
    fun tearDown() {
        dir.deleteRecursively()
    }

    private fun cache(directory: File? = dir): Cache = Cache(KEY, directory, logger) { SAVED_AT }

    private fun writeEntry(text: String) {
        cache().file!!.writeText(text)
    }

    @Test
    fun roundTripsResultsEtagAndContextHashWithSourceCache() {
        val c = cache()
        c.save(stored())
        assertEquals(StoredResults(results, "\"e1\"", Source.CACHE, "h1", null), c.load("h1"))
        assertEquals(emptyList<String>(), logger.lines)
    }

    @Test
    fun keepsANullEtag() {
        val c = cache()
        c.save(stored(etag = null))
        val loaded = c.load("h1")
        assertNotNull(loaded)
        assertNull(loaded!!.etag)
    }

    @Test
    fun writesExactlyTheEntryFields() {
        val c = cache()
        c.save(stored(etag = null))
        val entry = parseJson(c.file!!.readText()) as Map<*, *>
        assertEquals(setOf("v", "context_hash", "etag", "results", "saved_at"), entry.keys)
        assertEquals(1L, entry["v"])
        assertEquals("h1", entry["context_hash"])
        assertTrue(entry.containsKey("etag"))
        assertNull(entry["etag"])
        assertEquals("2026-11-14T12:00:00.000Z", entry["saved_at"])
        val items = entry["results"] as List<*>
        assertEquals(2, items.size)
        val first = items[0] as Map<*, *>
        assertEquals(setOf("key", "value", "variant", "reason", "error_code", "error_message"), first.keys)
        assertEquals("banner", first["key"])
    }

    @Test
    fun leavesNoTemporaryFileBehind() {
        val c = cache()
        c.save(stored())
        c.save(stored(etag = "\"e2\""))
        assertEquals(listOf(c.file!!.name), dir.list()!!.toList())
        assertEquals("\"e2\"", c.load("h1")?.etag)
    }

    @Test
    fun createsTheDirectoryOnFirstWrite() {
        val nested = File(dir, "a/b")
        val c = cache(nested)
        assertNull(c.load("h1"))
        assertFalse(nested.exists())
        c.save(stored())
        assertNotNull(c.load("h1"))
    }

    @Test
    fun returnsNullForAnotherContextHash() {
        val c = cache()
        c.save(stored())
        assertNull(c.load("h2"))
    }

    @Test
    fun returnsNullWhenNothingIsStored() {
        assertNull(cache().load("h1"))
        assertEquals(emptyList<String>(), logger.lines)
    }

    @Test
    fun returnsNullForAForeignVersion() {
        writeEntry("""{"v":2,"context_hash":"h1","etag":null,"results":[],"saved_at":"x"}""")
        assertNull(cache().load("h1"))
    }

    @Test
    fun returnsNullForACorruptFile() {
        for (text in listOf(
            "{not json",
            "[]",
            """{"v":1,"context_hash":"h1","etag":3,"results":[]}""",
            """{"v":1,"context_hash":"h1","results":[]}""",
            """{"v":1,"context_hash":"h1","etag":null}""",
            """{"v":1,"context_hash":"h1","etag":null,"results":[{"key":1}]}""",
        )) {
            writeEntry(text)
            assertNull(text, cache().load("h1"))
        }
        assertEquals(6, logger.lines.size)
        assertTrue(
            logger.lines.toString(),
            logger.lines.all { it.startsWith("debug megooflags: cache entry ignored: ") },
        )
    }

    @Test
    fun anUnwritableDirectoryLogsAWarningAndDoesNotThrow() {
        // A directory under a regular file can never be created, even as root.
        val file = File(dir, "file").apply { writeText("x") }
        val c = cache(File(file, "sub"))
        c.save(stored())
        assertNull(c.load("h1"))
        assertEquals(1, logger.lines.size)
        assertTrue(logger.lines[0], logger.lines[0].startsWith("warn megooflags: cache write failed: "))
        assertFalse(logger.lines[0].contains("secret123"))
    }

    @Test
    fun removeDeletesTheEntry() {
        val c = cache()
        c.save(stored())
        c.remove()
        assertNull(c.load("h1"))
        assertFalse(c.file!!.exists())
        c.remove()
        assertEquals(emptyList<String>(), logger.lines)
    }

    @Test
    fun theFileNameIsMegooflagsPlus16HexAndHasNoKey() {
        val f = cache().file!!
        assertTrue(f.name, Regex("^megooflags-[0-9a-f]{16}\\.json$").matches(f.name))
        assertFalse(f.path.contains("secret123"))
        assertEquals(dir, f.parentFile)
        // The first 16 hex of sha256("mf_client_secret123").
        assertEquals("megooflags-${sha256Hex(KEY).take(16)}.json", f.name)
    }

    @Test
    fun theFileNameDiffersPerSdkKey() {
        assertNotEquals(cache().file, Cache("mf_client_other", dir, logger).file)
    }

    @Test
    fun noDirectoryMeansNoReadsOrWrites() {
        val c = cache(null)
        assertNull(c.file)
        c.save(stored())
        assertNull(c.load("h1"))
        c.remove()
        assertEquals(emptyList<String>(), logger.lines)
    }
}
