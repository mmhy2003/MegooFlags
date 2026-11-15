# MegooFlags Android SDK

`io.github.mmhy2003:megooflags-android` is the MegooFlags client SDK for Android, an
[OpenFeature](https://openfeature.dev) provider. It sends the evaluation context with a client key and
receives the values the server evaluated, so no targeting rule ever reaches the device.

Status: 0.1.0, Alpha: under construction. Requires Android API 21 and Kotlin 2.1.

## Development

Build with JDK 17 and the Android SDK (`ANDROID_HOME` or `local.properties`):

```sh
./gradlew spotlessCheck lint testDebugUnitTest assembleRelease
```

## License

Apache 2.0, see [LICENSE](LICENSE).
