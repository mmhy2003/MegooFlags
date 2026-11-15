import 'dart:developer' as developer;

/// The severity of a log line.
enum MegooFlagsLogLevel { debug, info, warn, error }

/// A function the SDK logs through. Messages start with "megooflags: " and
/// never contain the SDK key.
typedef MegooFlagsLogger =
    void Function(MegooFlagsLogLevel level, String message);

/// The part of `dart:developer`'s `log` the default logger writes through.
typedef DeveloperLog = void Function(String message, {String name, int level});

/// developerLogger writes warnings (level 900) and errors (level 1000) to
/// [sink] under the name "megooflags"; debug and info are silent.
MegooFlagsLogger developerLogger(DeveloperLog sink) =>
    (level, message) => switch (level) {
      MegooFlagsLogLevel.debug || MegooFlagsLogLevel.info => null,
      MegooFlagsLogLevel.warn => sink(message, name: 'megooflags', level: 900),
      MegooFlagsLogLevel.error => sink(
        message,
        name: 'megooflags',
        level: 1000,
      ),
    };

/// defaultLogger writes warnings and errors with `dart:developer` `log`.
void defaultLogger(MegooFlagsLogLevel level, String message) =>
    _developerLogger(level, message);

final MegooFlagsLogger _developerLogger = developerLogger(developer.log);
