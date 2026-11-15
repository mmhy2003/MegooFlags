package io.github.mmhy2003.megooflags

import org.junit.Assert.assertEquals
import org.junit.Test

class MegooFlagsLoggerTest {
    @Test
    fun aCustomLoggerReceivesEachLevel() {
        val lines = mutableListOf<String>()
        val logger =
            object : MegooFlagsLogger {
                override fun debug(message: String) {
                    lines.add("debug $message")
                }

                override fun info(message: String) {
                    lines.add("info $message")
                }

                override fun warn(message: String) {
                    lines.add("warn $message")
                }

                override fun error(message: String) {
                    lines.add("error $message")
                }
            }
        logger.debug("a")
        logger.info("b")
        logger.warn("c")
        logger.error("d")
        assertEquals(listOf("debug a", "info b", "warn c", "error d"), lines)
    }

    @Test
    fun logcatLoggerUsesTheMegooFlagsTagAndRunsOnTheJvm() {
        assertEquals("MegooFlags", LogcatLogger.TAG)
        // android.util.Log is a stub returning defaults on the JVM; the calls must not throw.
        LogcatLogger.debug("megooflags: a")
        LogcatLogger.info("megooflags: b")
        LogcatLogger.warn("megooflags: c")
        LogcatLogger.error("megooflags: d")
    }
}
