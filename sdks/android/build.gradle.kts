import com.vanniktech.maven.publish.AndroidSingleVariantLibrary
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import org.jetbrains.kotlin.gradle.dsl.KotlinVersion

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.spotless)
    alias(libs.plugins.vanniktech.publish)
}

android {
    namespace = "io.github.mmhy2003.megooflags"
    compileSdk = 35

    defaultConfig {
        minSdk = 21
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    explicitApi()
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_11)
        languageVersion.set(KotlinVersion.KOTLIN_2_1)
        apiVersion.set(KotlinVersion.KOTLIN_2_1)
    }
}

dependencies {
    api(libs.openfeature.kotlin.sdk)
    implementation(libs.kotlinx.coroutines.core)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.json)
}

tasks.withType<Test>().configureEach {
    if (project.hasProperty("e2e")) {
        filter { includeTestsMatching("io.github.mmhy2003.megooflags.e2e.*") }
    } else {
        exclude("**/e2e/**")
    }
    systemProperty("spec.dir", rootDir.resolve("../../spec/client").absolutePath)
}

spotless {
    kotlin {
        target("src/**/*.kt")
        ktlint()
    }
    kotlinGradle {
        ktlint()
    }
}

mavenPublishing {
    configure(AndroidSingleVariantLibrary("release", sourcesJar = true, publishJavadocJar = true))
    publishToMavenCentral()
    signAllPublications()
    coordinates(
        property("GROUP").toString(),
        property("POM_ARTIFACT_ID").toString(),
        property("VERSION_NAME").toString(),
    )
    // The POM fields come from the POM_* entries in gradle.properties.
}
