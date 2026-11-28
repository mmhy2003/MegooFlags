// A text/event-stream parser over a byte stream (the stream response body).
import 'dart:convert';
import 'dart:typed_data';

/// One dispatched server-sent event. name is "message" by default.
final class SseEvent {
  const SseEvent({required this.name, required this.data, required this.id});

  final String name;
  final String data;
  final String id;

  @override
  bool operator ==(Object other) =>
      other is SseEvent &&
      other.name == name &&
      other.data == data &&
      other.id == id;

  @override
  int get hashCode => Object.hash(name, data, id);

  @override
  String toString() => 'SseEvent($name, $data, $id)';
}

const _lf = 0x0a;
const _cr = 0x0d;
final _digits = RegExp(r'^[0-9]+$');

/// SseParser parses the chunks fed to it. Lines are split on the LF byte
/// before decoding, so a character split across chunks decodes whole, and a
/// line of any length is accepted; a trailing CR is removed and a BOM is
/// kept as a character. An error thrown by onEvent is passed through feed
/// and stops reading. finish discards an unterminated last line or event.
class SseParser {
  SseParser({
    required void Function() onLine,
    required void Function(int ms) onRetry,
    required void Function(SseEvent event) onEvent,
  }) : _onLine = onLine,
       _onRetry = onRetry,
       _onEvent = onEvent;

  /// Runs for every line, comments included (the dead timer).
  final void Function() _onLine;

  /// Receives a valid retry field, in milliseconds.
  final void Function(int ms) _onRetry;

  /// Receives each event.
  final void Function(SseEvent event) _onEvent;

  final _pending = BytesBuilder();
  String _name = '';
  String _id = '';
  final _data = <String>[];
  bool _seen = false;

  /// feed parses the bytes of [chunk].
  void feed(List<int> chunk) {
    var start = 0;
    while (true) {
      final end = chunk.indexOf(_lf, start);
      if (end < 0) {
        if (start < chunk.length) {
          _pending.add(chunk.sublist(start));
        }
        return;
      }
      List<int> bytes;
      if (_pending.isNotEmpty) {
        _pending.add(chunk.sublist(start, end));
        bytes = _pending.takeBytes();
      } else {
        bytes = Uint8List.sublistView(
          chunk is Uint8List ? chunk : Uint8List.fromList(chunk),
          start,
          end,
        );
      }
      if (bytes.isNotEmpty && bytes.last == _cr) {
        bytes = bytes.sublist(0, bytes.length - 1);
      }
      start = end + 1;
      _line(utf8.decode(bytes, allowMalformed: true));
    }
  }

  /// finish ends the stream: an unterminated line or event is dropped.
  void finish() {
    _pending.clear();
    _reset();
  }

  void _line(String line) {
    _onLine();
    if (line.isEmpty) {
      _dispatch();
      return;
    }
    if (line.startsWith(':')) {
      return;
    }
    var field = line;
    var value = '';
    final colon = line.indexOf(':');
    if (colon >= 0) {
      field = line.substring(0, colon);
      value = line.substring(colon + 1);
      if (value.startsWith(' ')) {
        value = value.substring(1);
      }
    }
    switch (field) {
      case 'event':
        _name = value;
      case 'data':
        _data.add(value);
        _seen = true;
      case 'id':
        if (!value.contains('\u0000')) _id = value;
      case 'retry':
        if (_digits.hasMatch(value)) {
          final ms = int.tryParse(value);
          if (ms != null) _onRetry(ms);
        }
    }
  }

  void _dispatch() {
    final event = _seen
        ? SseEvent(
            name: _name.isEmpty ? 'message' : _name,
            data: _data.join('\n'),
            id: _id,
          )
        : null;
    _reset();
    if (event != null) {
      _onEvent(event);
    }
  }

  void _reset() {
    _name = '';
    _id = '';
    _data.clear();
    _seen = false;
  }
}
