import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/sse.dart';

class Seen {
  int lines = 0;
  final retries = <int>[];
  final events = <SseEvent>[];
}

/// read feeds [parts] (strings are UTF-8 encoded) to a parser, then finishes.
Seen read(List<Object> parts) {
  final seen = Seen();
  final p = SseParser(
    onLine: () => seen.lines++,
    onRetry: seen.retries.add,
    onEvent: seen.events.add,
  );
  for (final part in parts) {
    p.feed(part is String ? utf8.encode(part) : part as List<int>);
  }
  p.finish();
  return seen;
}

SseEvent ev(String name, String data, [String id = '']) =>
    SseEvent(name: name, data: data, id: id);

void main() {
  final cases = <(String, String, int, List<SseEvent>)>[
    (
      'named event with id',
      'event: put\nid: 42\ndata: {"flags":{}}\n\n',
      4,
      [ev('put', '{"flags":{}}', '42')],
    ),
    ('default name', 'data: x\n\n', 2, [ev('message', 'x')]),
    (
      'multi-line data',
      'data: a\ndata: b\ndata:c\n\n',
      4,
      [ev('message', 'a\nb\nc')],
    ),
    (
      'CRLF endings',
      'event: put\r\nid: 7\r\ndata: x\r\n\r\n',
      4,
      [ev('put', 'x', '7')],
    ),
    (
      'unknown event name passed through',
      'event: mystery\ndata: 1\n\n',
      3,
      [ev('mystery', '1')],
    ),
    (
      'unterminated last event discarded',
      'data: a\n\nevent: put\ndata: b\n',
      4,
      [ev('message', 'a')],
    ),
    (
      'unterminated last line discarded',
      'data: a\n\ndata: b',
      2,
      [ev('message', 'a')],
    ),
    (
      'state resets after dispatch',
      'event: put\nid: 1\ndata: a\n\ndata: b\n\n',
      6,
      [ev('put', 'a', '1'), ev('message', 'b')],
    ),
    (
      'empty line without data dispatches nothing',
      'event: put\n\ndata: a\n\n',
      4,
      [ev('message', 'a')],
    ),
    (
      'id with NUL ignored',
      'id: a\u0000b\ndata: x\n\n',
      3,
      [ev('message', 'x')],
    ),
    (
      'field without colon has an empty value',
      'data\ndata\n\n',
      3,
      [ev('message', '\n')],
    ),
    (
      'only one leading space removed, unknown field ignored',
      'foo: bar\ndata:  two\n\n',
      3,
      [ev('message', ' two')],
    ),
    (
      'colon inside the value kept',
      'data: a: b\n\n',
      2,
      [ev('message', 'a: b')],
    ),
  ];
  for (final (name, input, lines, events) in cases) {
    test(name, () {
      final seen = read([input]);
      expect(seen.events, events);
      expect(seen.lines, lines);
      expect(seen.retries, isEmpty);
    });
  }

  test('comments call onLine only', () {
    final seen = read([': ping\n:\n\n']);
    expect(seen.lines, 3);
    expect(seen.retries, isEmpty);
    expect(seen.events, isEmpty);
  });

  test('a valid retry calls onRetry in milliseconds', () {
    final seen = read(['retry: 3000\n\n']);
    expect(seen.lines, 2);
    expect(seen.retries, [3000]);
    expect(seen.events, isEmpty);
  });

  for (final input in [
    'retry: x\n',
    'retry: -1\n',
    'retry: 1.5\n',
    'retry:\n',
  ]) {
    test('an invalid retry ${jsonEncode(input)} is ignored', () {
      expect(read([input]).retries, isEmpty);
    });
  }

  test('finish discards an event that a later feed would have ended', () {
    final seen = Seen();
    final p = SseParser(
      onLine: () => seen.lines++,
      onRetry: seen.retries.add,
      onEvent: seen.events.add,
    );
    p.feed(utf8.encode('event: put\ndata: a\n'));
    p.finish();
    p.feed(utf8.encode('\n'));
    expect(seen.events, isEmpty);
  });

  test('reads a 1 MiB data line', () {
    final big = 'x' * (1 << 20);
    final parts = <Object>['event: put\ndata: '];
    for (var i = 0; i < big.length; i += 16384) {
      parts.add(big.substring(i, i + 16384));
    }
    parts.add('\n\n');
    final seen = read(parts);
    expect(seen.events, hasLength(1));
    expect(seen.events.single.data, big);
  });

  test('decodes a multi-byte character split across two chunks', () {
    final bytes = utf8.encode('data: é€😀\n\n');
    final at = bytes.indexOf(0xe2) + 1;
    final seen = read([bytes.sublist(0, at), bytes.sublist(at)]);
    expect(seen.events, [ev('message', 'é€😀')]);
  });

  test('splits lines that arrive in one chunk or byte by byte', () {
    const text = 'event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n';
    final one = read([text]);
    final split = read([
      for (final b in utf8.encode(text)) [b],
    ]);
    expect(split.events, one.events);
    expect(split.lines, one.lines);
    expect(one.events.map((e) => e.data), ['a', 'b']);
  });

  test('an error thrown by onEvent stops reading', () {
    final names = <String>[];
    final stop = StateError('stop');
    final p = SseParser(
      onLine: () {},
      onRetry: (_) {},
      onEvent: (e) {
        names.add(e.name);
        throw stop;
      },
    );
    expect(
      () => p.feed(
        utf8.encode('event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n'),
      ),
      throwsA(same(stop)),
    );
    expect(names, ['revoked']);
  });

  test('events are equal by value', () {
    expect(ev('put', 'a', '1'), ev('put', 'a', '1'));
    expect(ev('put', 'a', '1').hashCode, ev('put', 'a', '1').hashCode);
    expect(ev('put', 'a', '1'), isNot(ev('put', 'a', '2')));
    expect(ev('put', 'a', '1').toString(), 'SseEvent(put, a, 1)');
  });
}
