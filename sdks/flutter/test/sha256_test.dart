import 'dart:convert';

import 'package:crypto/crypto.dart' as crypto;
import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/sha256.dart';

String reference(String text) =>
    crypto.sha256.convert(utf8.encode(text)).toString();

void main() {
  final vectors = {
    'the empty string': (
      '',
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    ),
    'abc': (
      'abc',
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    ),
    'the 448-bit message': (
      'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq',
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
    ),
    'one million a': (
      'a' * 1000000,
      'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0',
    ),
  };
  vectors.forEach((name, v) {
    test('matches the NIST vector for $name', () {
      expect(sha256Hex(v.$1), v.$2);
    });
  });

  for (final n in [55, 56, 63, 64, 65]) {
    test('matches package:crypto around the padding boundary ($n bytes)', () {
      final text = 'x' * n;
      expect(sha256Hex(text), reference(text));
    });
  }

  test('hashes the UTF-8 bytes of non-ASCII text', () {
    const text = 'café 日本 😀 "～"\u0001';
    expect(sha256Hex(text), reference(text));
  });
}
