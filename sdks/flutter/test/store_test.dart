import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/store.dart';

EvaluateResult r(String key, Object? value, [String? variant = 'v']) =>
    EvaluateResult(
      key: key,
      value: value,
      variant: variant,
      reason: 'TARGETING_MATCH',
    );

StoredResults stored(List<EvaluateResult> results) => StoredResults.fromList(
  results,
  etag: '"e1"',
  source: Source.network,
  contextHash: 'h',
  targetingKey: 'u1',
);

void main() {
  test('StoredResults keys the results by flag key', () {
    final s = stored([r('a', 1), r('b', 2)]);
    expect(s.results.keys, ['a', 'b']);
    expect(s.results['b']!.value, 2);
    expect(s.etag, '"e1"');
    expect(s.source, Source.network);
    expect(s.contextHash, 'h');
    expect(s.targetingKey, 'u1');
  });

  test('Store holds one StoredResults or none and swaps it', () {
    final store = Store();
    expect(store.current, isNull);
    final first = stored([r('a', 1)]);
    final second = stored([r('a', 2)]);
    store.current = first;
    expect(store.current, same(first));
    store.current = second;
    expect(store.current, same(second));
    store.current = null;
    expect(store.current, isNull);
  });

  group('changedFlags', () {
    test('is empty when values and variants are equal', () {
      expect(
        changedFlags(
          stored([
            r('a', 1),
            r('b', {
              'x': [1, 2],
            }),
            r('c', null, null),
          ]),
          stored([
            r('a', 1.0),
            r('b', {
              'x': [1, 2.0],
            }),
            r('c', null, null),
          ]),
        ),
        isEmpty,
      );
    });

    test(
      'lists, sorted, changed values, changed variants and one-sided keys',
      () {
        expect(
          changedFlags(
            stored([
              r('e', true),
              r('d', 1, 'one'),
              r('c', 'x'),
              r('a', 1),
              r('b', [1]),
            ]),
            stored([
              r('e', 1),
              r('d', 1, 'uno'),
              r('z', 'new'),
              r('a', 1),
              r('b', {'0': 1}),
            ]),
          ),
          ['b', 'c', 'd', 'e', 'z'],
        );
      },
    );
  });

  group('sameJson', () {
    final pairs = <(Object?, Object?, bool)>[
      (1, 1.0, true),
      (true, 1, false),
      ('1', 1, false),
      (null, null, true),
      (null, false, false),
      (<String, Object?>{}, <Object?>[], false),
      ({'a': 1}, {'a': 1, 'b': 2}, false),
      ({'a': 1}, {'b': 1}, false),
      ({'a': null}, {'b': null}, false),
      ([1, 2], [1, 2], true),
      ([1, 2], [2, 1], false),
    ];
    for (final (a, b, want) in pairs) {
      test('sameJson($a, $b) is $want', () {
        expect(sameJson(a, b), want);
        expect(sameJson(b, a), want);
      });
    }
  });
}
