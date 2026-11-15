package io.github.mmhy2003.megooflags

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File
import java.util.Properties

class VersionTest {
    @Test
    fun versionIsTheReleaseVersion() {
        assertEquals("0.1.0", VERSION)
    }

    @Test
    fun versionMatchesGradleProperties() {
        val properties = Properties()
        File("gradle.properties").inputStream().use { properties.load(it) }
        assertEquals(properties.getProperty("VERSION_NAME"), VERSION)
    }
}
