import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/megooflags.dart';

void main() {
  test('the SDK version is 0.1.0', () {
    expect(megooFlagsVersion, '0.1.0');
  });

  test('the SDK version matches pubspec.yaml', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final match = RegExp(
      r'''^version:\s*['"]?([^'"\s]+)['"]?\s*$''',
      multiLine: true,
    ).firstMatch(pubspec);
    expect(match, isNotNull);
    expect(megooFlagsVersion, match!.group(1));
  });
}
