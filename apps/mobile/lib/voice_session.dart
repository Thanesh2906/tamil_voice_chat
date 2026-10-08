import 'dart:async';
import 'dart:math';

String _newVoiceId() {
  final random = Random.secure();
  return 'mobile-${List.generate(16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0')).join()}';
}

/// Socket/project-scoped conversation identity and per-turn readiness gate.
class VoiceSession {
  VoiceSession({String Function()? newId}) : _newId = newId ?? _newVoiceId;

  final String Function() _newId;
  String sessionId = '';
  String? requestId;
  String? projectId;
  bool authenticated = false;
  bool recordingReady = false;
  Completer<bool>? _ready;

  void newConnection() {
    disconnect();
    sessionId = _newId();
  }

  void disconnect() {
    authenticated = false;
    invalidateTurn();
  }

  void selectProject(String? value) {
    if (projectId == value && sessionId.isNotEmpty) return;
    invalidateTurn();
    projectId = value;
    sessionId = _newId();
  }

  String beginTurn(String? project) {
    if (!authenticated) throw StateError('Voice connection is not authenticated');
    selectProject(project);
    invalidateTurn();
    requestId = _newId();
    _ready = Completer<bool>();
    return requestId!;
  }

  bool matches(Map<String, dynamic> message) => requestId != null &&
      message['request_id'] == requestId && message['session_id'] == sessionId;

  bool acknowledge(Map<String, dynamic> message) {
    if (message['type'] != 'ready' || !authenticated || !matches(message) || _ready == null || _ready!.isCompleted) return false;
    recordingReady = true;
    _ready!.complete(true);
    return true;
  }

  Future<bool> waitForReady({Duration timeout = const Duration(seconds: 10)}) async {
    final pending = _ready;
    if (pending == null) return false;
    try {
      return await pending.future.timeout(timeout);
    } on TimeoutException {
      if (identical(pending, _ready)) cancelPendingStart();
      return false;
    }
  }

  void cancelPendingStart() {
    final pending = _ready;
    if (pending != null && !pending.isCompleted) {
      recordingReady = false;
      pending.complete(false);
    }
  }

  void invalidateTurn() {
    cancelPendingStart();
    recordingReady = false;
    _ready = null;
    requestId = null;
  }
}
