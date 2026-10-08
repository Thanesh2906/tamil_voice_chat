import 'dart:async';
import 'dart:typed_data';

/// Serializes microphone operations and binds every PCM frame to one session.
///
/// Keeping this independent of platform plugins makes interrupted permission,
/// recorder startup, and shutdown paths testable without a physical microphone.
class VoiceCapture {
  VoiceCapture({
    required this.requestPermission,
    required this.openRecorder,
    required this.startRecorder,
    required this.stopRecorder,
    required this.closeRecorder,
  });

  final Future<bool> Function() requestPermission;
  final Future<void> Function() openRecorder;
  final Future<void> Function(StreamSink<Uint8List>) startRecorder;
  final Future<void> Function() stopRecorder;
  final Future<void> Function() closeRecorder;

  Future<void> _pending = Future<void>.value();
  StreamController<Uint8List>? _audio;
  bool Function()? _canSend;
  void Function(bool cancelled)? _sendEnd;
  void Function()? _cancelPendingStart;
  bool _requested = false;
  bool _opened = false;
  bool _started = false;
  bool _startSent = false;
  bool _disposed = false;
  int _generation = 0;

  bool get isRecording => _started && _requested;

  Future<void> _enqueue(Future<void> Function() operation) {
    final result = _pending.then((_) => operation());
    // A failed operation must not prevent subsequent cleanup or another turn.
    _pending = result.catchError((Object _) {});
    return result;
  }

  Future<void> start({
    required bool Function() canSend,
    required void Function() sendStart,
    required void Function(Uint8List) sendAudio,
    required void Function(bool cancelled) sendEnd,
    Future<bool> Function()? waitForReady,
    void Function()? cancelPendingStart,
  }) {
    if (_disposed || _requested) return Future<void>.value();
    _requested = true;
    final generation = ++_generation;
    bool isCurrent() => !_disposed && _requested && generation == _generation;

    return _enqueue(() async {
      if (!isCurrent()) return;
      try {
        final permitted = await requestPermission();
        if (!isCurrent()) return;
        if (!permitted) throw StateError('Microphone permission is required.');
        if (!canSend()) {
          _requested = false;
          return;
        }
        // Even a partially failed open must go through closeRecorder.
        _opened = true;
        await openRecorder();
        if (!isCurrent() || !canSend()) {
          if (isCurrent()) _requested = false;
          await _cleanup(cancelled: true);
          return;
        }
        _canSend = canSend;
        _sendEnd = sendEnd;
        _cancelPendingStart = cancelPendingStart;
        final audio = StreamController<Uint8List>();
        _audio = audio;
        audio.stream.listen((bytes) {
          if (!_disposed && canSend() && bytes.isNotEmpty) sendAudio(bytes);
        });
        // The server must see the turn metadata before the very first PCM frame.
        sendStart();
        _startSent = true;
        if (waitForReady != null) {
          final ready = await waitForReady();
          if (!isCurrent() || !canSend()) {
            if (isCurrent()) _requested = false;
            await _cleanup(cancelled: true);
            return;
          }
          if (!ready) throw StateError('Voice server did not accept the recording.');
        }
        _started = true;
        await startRecorder(audio.sink);
        if (!isCurrent() || !canSend()) {
          if (isCurrent()) _requested = false;
          await _cleanup(cancelled: true);
        }
      } catch (_) {
        if (generation == _generation) _requested = false;
        await _cleanup(cancelled: true);
        rethrow;
      }
    });
  }

  Future<void> stop({bool cancelled = false}) {
    _requested = false;
    ++_generation;
    _cancelPendingStart?.call();
    return _enqueue(() => _cleanup(cancelled: cancelled));
  }

  Future<void> _cleanup({required bool cancelled}) async {
    try {
      if (_started) await stopRecorder();
    } finally {
      _started = false;
      try {
        if (_opened) await closeRecorder();
      } finally {
        _opened = false;
        final audio = _audio;
        _audio = null;
        // Drain queued PCM before sending the final stop control frame.
        await audio?.close();
        final sendEnd = _sendEnd;
        final canSend = _canSend;
        final startSent = _startSent;
        _sendEnd = null;
        _cancelPendingStart = null;
        _canSend = null;
        _startSent = false;
        if (!_disposed && startSent && (canSend?.call() ?? false)) {
          sendEnd?.call(cancelled);
        }
      }
    }
  }

  Future<void> dispose() {
    _disposed = true;
    return stop(cancelled: true);
  }
}
