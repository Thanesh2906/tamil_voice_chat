import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/voice_session.dart';

VoiceSession session() {
  var counter = 0;
  return VoiceSession(newId: () => 'test-${++counter}');
}

Map<String, dynamic> readyEvent(VoiceSession state) => {
  'type': 'ready', 'request_id': state.requestId, 'session_id': state.sessionId,
};

void main() {
  test('authentication is required before a recording turn', () {
    final state = session()..newConnection();
    expect(() => state.beginTurn('project-a'), throwsStateError);
  });

  test('project changes rotate conversations, same-project turns retain them', () {
    final state = session()..newConnection();
    state.authenticated = true;
    final firstRequest = state.beginTurn('project-a');
    final firstSession = state.sessionId;
    state.beginTurn('project-a');
    expect(state.sessionId, firstSession);
    expect(state.requestId, isNot(firstRequest));
    state.beginTurn('project-b');
    expect(state.sessionId, isNot(firstSession));
    final secondSession = state.sessionId;
    state.beginTurn('project-a');
    expect(state.sessionId, isNot(secondSession));
    expect(state.sessionId, isNot(firstSession));
  });

  test('reconnect rotates identity and invalidates old messages', () async {
    final state = session()..newConnection();
    state.authenticated = true;
    state.beginTurn('project-a');
    final old = readyEvent(state);
    final pending = state.waitForReady();
    state.newConnection();
    expect(await pending, isFalse);
    expect(state.authenticated, isFalse);
    expect(state.matches(old), isFalse);
    expect(state.sessionId, isNot(old['session_id']));
  });

  test('only the matching ready acknowledges a turn', () async {
    final state = session()..newConnection();
    state.authenticated = true;
    state.beginTurn('project-a');
    final pending = state.waitForReady();
    expect(state.acknowledge({...readyEvent(state), 'request_id': 'stale'}), isFalse);
    expect(state.acknowledge({...readyEvent(state), 'session_id': 'stale'}), isFalse);
    expect(state.acknowledge({...readyEvent(state), 'type': 'token'}), isFalse);
    expect(state.recordingReady, isFalse);
    expect(state.acknowledge(readyEvent(state)), isTrue);
    expect(await pending, isTrue);
  });

  test('cancelled start rejects late readiness and does not hang', () async {
    final state = session()..newConnection();
    state.authenticated = true;
    state.beginTurn(null);
    final old = readyEvent(state);
    final pending = state.waitForReady();
    state.invalidateTurn();
    expect(await pending, isFalse);
    expect(state.acknowledge(old), isFalse);
    expect(state.recordingReady, isFalse);
  });

  test('readiness timeout leaves no recording permission', () async {
    final state = session()..newConnection();
    state.authenticated = true;
    state.beginTurn(null);
    expect(await state.waitForReady(timeout: Duration.zero), isFalse);
    expect(state.acknowledge(readyEvent(state)), isFalse);
    expect(state.recordingReady, isFalse);
  });

  test('normal stop cancellation hook preserves acknowledged final PCM', () {
    final state = session()..newConnection();
    state.authenticated = true;
    state.beginTurn(null);
    state.acknowledge(readyEvent(state));
    state.cancelPendingStart();
    expect(state.recordingReady, isTrue);
    state.invalidateTurn();
    expect(state.recordingReady, isFalse);
  });
}
