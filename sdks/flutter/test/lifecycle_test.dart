import 'package:fake_async/fake_async.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/lifecycle.dart';

/// A LifecycleSource the test drives.
class FakeSource implements LifecycleSource {
  void Function(BackgroundState)? listener;
  int unsubscribes = 0;

  void emit(BackgroundState s) => listener?.call(s);

  @override
  void Function() subscribe(void Function(BackgroundState) l) {
    listener = l;
    return () {
      unsubscribes++;
      listener = null;
    };
  }
}

void main() {
  // Runs first: the binding is initialised by a later group and stays so.
  test('AppLifecycleSource gives no signal without a WidgetsBinding', () {
    final states = <BackgroundState>[];
    final unsubscribe = AppLifecycleSource().subscribe(states.add);
    unsubscribe();
    expect(states, isEmpty);
  });

  group('AppLifecycleSource', () {
    late TestWidgetsFlutterBinding binding;

    setUpAll(() {
      binding = TestWidgetsFlutterBinding.ensureInitialized();
    });

    test('reports hidden on hide and visible on show', () {
      binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      final states = <BackgroundState>[];
      final unsubscribe = AppLifecycleSource().subscribe(states.add);

      binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      expect(states, [BackgroundState.hidden]);

      binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      expect(states, [BackgroundState.hidden, BackgroundState.visible]);

      unsubscribe();
      binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      expect(states, [BackgroundState.hidden, BackgroundState.visible]);
      binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    });
  });

  group('BackgroundController', () {
    late FakeSource source;
    late int flushes;
    late int pauses;
    late int resumes;

    BackgroundController controller(LifecycleSource? s) => BackgroundController(
      source: s,
      flush: () async => flushes++,
      pause: () => pauses++,
      resume: () => resumes++,
    );

    setUp(() {
      source = FakeSource();
      flushes = 0;
      pauses = 0;
      resumes = 0;
    });

    test('hidden flushes at once and pauses after 30 s', () {
      fakeAsync((async) {
        controller(source).start();
        source.emit(BackgroundState.hidden);
        async.flushMicrotasks();
        expect(flushes, 1);
        async.elapse(const Duration(seconds: 29));
        expect(pauses, 0);
        async.elapse(const Duration(seconds: 1));
        expect(pauses, 1);
        expect(resumes, 0);
      });
    });

    test('visible within 30 s cancels the pause', () {
      fakeAsync((async) {
        controller(source).start();
        source.emit(BackgroundState.hidden);
        async.elapse(const Duration(seconds: 10));
        source.emit(BackgroundState.visible);
        async.elapse(const Duration(minutes: 1));
        expect(pauses, 0);
        expect(resumes, 0);
      });
    });

    test('visible after the pause resumes once', () {
      fakeAsync((async) {
        controller(source).start();
        source.emit(BackgroundState.hidden);
        async.elapse(const Duration(seconds: 30));
        source.emit(BackgroundState.hidden);
        async.elapse(const Duration(seconds: 30));
        expect(pauses, 1);
        expect(flushes, 2);
        source.emit(BackgroundState.visible);
        source.emit(BackgroundState.visible);
        expect(resumes, 1);
      });
    });

    test('a failing flush is ignored', () {
      fakeAsync((async) {
        BackgroundController(
          source: source,
          flush: () => Future.error(StateError('send failed')),
          pause: () => pauses++,
          resume: () => resumes++,
        ).start();
        source.emit(BackgroundState.hidden);
        async.elapse(const Duration(seconds: 30));
        expect(pauses, 1);
      });
    });

    test('start is idempotent and stop unsubscribes and cancels the pause', () {
      fakeAsync((async) {
        var subscribes = 0;
        final counting = _CountingSource(source, () => subscribes++);
        final c = controller(counting)..start();
        c.start();
        expect(subscribes, 1);
        source.emit(BackgroundState.hidden);
        c.stop();
        expect(source.unsubscribes, 1);
        async.elapse(const Duration(minutes: 1));
        expect(pauses, 0);
        c.stop();
        expect(source.unsubscribes, 1);
      });
    });

    test('without a source start and stop do nothing', () {
      final c = controller(null)..start();
      c.stop();
      expect([flushes, pauses, resumes], [0, 0, 0]);
    });
  });
}

class _CountingSource implements LifecycleSource {
  _CountingSource(this._inner, this._onSubscribe);

  final LifecycleSource _inner;
  final void Function() _onSubscribe;

  @override
  void Function() subscribe(void Function(BackgroundState) l) {
    _onSubscribe();
    return _inner.subscribe(l);
  }
}
