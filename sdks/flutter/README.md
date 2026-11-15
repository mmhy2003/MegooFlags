# MegooFlags Flutter SDK

`megooflags` is the MegooFlags client SDK for Flutter, a pub package that will ship an
[OpenFeature](https://openfeature.dev) provider. Values are evaluated by the server for the context you
pass, with a client key, so no targeting rule ever reaches the device. They will follow changes over SSE
with a polling fallback, be cached on the device, start from bootstrap values, and send exposures in
batches. Status: 0.1.0, Alpha, under construction; requires Dart 3.10 and Flutter 3.38.
