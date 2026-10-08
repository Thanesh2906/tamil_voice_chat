import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/voice_capture.dart';

class FakeRecorder {
  final events = <String>[];
  Completer<bool>? permission;
  Completer<void>? opening;
  Completer<void>? starting;
  Completer<bool>? ready;
  bool failOpen = false;
  bool failStart = false;
  bool failStop = false;
  bool connected = true;
  StreamSink<Uint8List>? sink;

  late final capture = VoiceCapture(
    requestPermission: () async {
      events.add('permission');
      return permission == null ? true : await permission!.future;
    },
    openRecorder: () async {
      events.add('open');
      if (failOpen) throw StateError('Recorder open failed');
      if (opening != null) await opening!.future;
    },
    startRecorder: (value) async {
      events.add('record');
      sink = value;
      value.add(Uint8List.fromList([0, 1]));
      if (failStart) throw StateError('Recorder unavailable');
      if (starting != null) await starting!.future;
    },
    stopRecorder: () async {
      events.add('stopRecorder');
      if (failStop) throw StateError('Recorder stop failed');
      sink?.add(Uint8List.fromList([2, 3]));
    },
    closeRecorder: () async { events.add('close'); },
  );

  Future<void> start() => capture.start(
    canSend: () => connected,
    sendStart: () => events.add('start'),
    sendAudio: (_) => events.add('pcm'),
    sendEnd: (cancelled) => events.add(cancelled ? 'cancel' : 'stop'),
    waitForReady: ready == null ? null : () => ready!.future,
    cancelPendingStart: () {
      if (ready != null && !ready!.isCompleted) ready!.complete(false);
    },
  );
}

Future<void> drainTasks() => Future<void>.delayed(Duration.zero);

void main() {

  test('waits for ready before starting native capture or sending PCM', () async {
    final fake = FakeRecorder()..ready = Completer<bool>();
    final starting = fake.start();
    await drainTasks();
    expect(fake.events, ['permission', 'open', 'start']);
    expect(fake.capture.isRecording, isFalse);
    fake.ready!.complete(true);
    await starting;
    expect(fake.events, containsAllInOrder(['start', 'record', 'pcm']));
    await fake.capture.stop();
  });

  test('rejected readiness closes the recorder without recording', () async {
    final fake = FakeRecorder()..ready = Completer<bool>();
    final starting = fake.start();
    final rejected = expectLater(starting, throwsStateError);
    await drainTasks();
    fake.ready!.complete(false);
    await rejected;
    expect(fake.events, ['permission', 'open', 'start', 'close', 'cancel']);
  });

  test('cancel while awaiting ready unblocks cleanup and never records', () async {
    final fake = FakeRecorder()..ready = Completer<bool>();
    final starting = fake.start();
    await drainTasks();
    final stopping = fake.capture.stop(cancelled: true);
    await Future.wait([starting, stopping]);
    expect(fake.events, ['permission', 'open', 'start', 'close', 'cancel']);
    expect(fake.capture.isRecording, isFalse);
  });

  test('sends start before PCM and drains the recorder before stop', () async {
    final fake = FakeRecorder();

    await fake.start();
    expect(fake.capture.isRecording, isTrue);
    await fake.capture.stop();

    expect(fake.events.indexOf('start'), lessThan(fake.events.indexOf('pcm')));
    expect(fake.events.where((event) => event == 'pcm').length, 2);
    expect(fake.events.last, 'stop');
    expect(fake.events.indexOf('close'), lessThan(fake.events.indexOf('stop')));
    expect(fake.capture.isRecording, isFalse);
  });

  test('duplicate starts request permission and open the recorder once', () async {
    final fake = FakeRecorder()..permission = Completer<bool>();
    final first = fake.start();
    await fake.start();
    await drainTasks();
    fake.permission!.complete(true);
    await first;
    await fake.capture.stop();

    expect(fake.events.where((event) => event == 'permission').length, 1);
    expect(fake.events.where((event) => event == 'open').length, 1);
  });

  test('stop during permission prompt never opens the microphone', () async {
    final fake = FakeRecorder()..permission = Completer<bool>();
    final starting = fake.start();
    await drainTasks();
    final stopping = fake.capture.stop(cancelled: true);
    fake.permission!.complete(true);
    await Future.wait([starting, stopping]);

    expect(fake.events, ['permission']);
    expect(fake.capture.isRecording, isFalse);
  });

  test('stop during open waits, closes, and never starts capture', () async {
    final fake = FakeRecorder()..opening = Completer<void>();
    final starting = fake.start();
    await drainTasks();
    final stopping = fake.capture.stop(cancelled: true);
    expect(fake.events, ['permission', 'open']);
    fake.opening!.complete();
    await Future.wait([starting, stopping]);

    expect(fake.events, ['permission', 'open', 'close']);
  });

  test('disconnect during startup drops PCM and closes the recorder', () async {
    final fake = FakeRecorder()..starting = Completer<void>();
    final starting = fake.start();
    await drainTasks();
    fake.connected = false;
    final stopping = fake.capture.stop(cancelled: true);
    fake.sink!.add(Uint8List.fromList([4, 5]));
    fake.starting!.complete();
    await Future.wait([starting, stopping]);

    expect(fake.events.where((event) => event == 'pcm').length, 1);
    expect(fake.events.last, 'close');
    expect(fake.events, isNot(contains('stop')));
    expect(fake.events, isNot(contains('cancel')));
    expect(fake.capture.isRecording, isFalse);
  });

  test('failed startup cleans up and allows a later recording', () async {
    final fake = FakeRecorder()..failStart = true;

    await expectLater(fake.start(), throwsStateError);
    expect(fake.events, containsAllInOrder(['stopRecorder', 'close', 'cancel']));
    expect(fake.capture.isRecording, isFalse);

    fake.failStart = false;
    await fake.start();
    expect(fake.capture.isRecording, isTrue);
    await fake.capture.stop();
    expect(fake.events.last, 'stop');
  });

  test('failed recorder open still closes the native recorder', () async {
    final fake = FakeRecorder()..failOpen = true;

    await expectLater(fake.start(), throwsStateError);
    expect(fake.events, ['permission', 'open', 'close']);
    expect(fake.capture.isRecording, isFalse);
  });

  test('failed recorder stop still closes and drains the session', () async {
    final fake = FakeRecorder();
    await fake.start();
    fake.failStop = true;

    await expectLater(fake.capture.stop(), throwsStateError);
    expect(fake.events, containsAllInOrder(['stopRecorder', 'close', 'stop']));
    expect(fake.capture.isRecording, isFalse);
  });

  test('a fresh start waits for the previous stop to finish', () async {
    final fake = FakeRecorder();
    await fake.start();
    final stopping = fake.capture.stop();
    final restarting = fake.start();
    await Future.wait([stopping, restarting]);

    final previousStop = fake.events.indexOf('stop');
    final nextStart = fake.events.lastIndexOf('start');
    expect(previousStop, lessThan(nextStart));
    expect(fake.capture.isRecording, isTrue);
    await fake.capture.stop();
  });

  test('denied permission does not leave a pending capture', () async {
    final fake = FakeRecorder()..permission = Completer<bool>();
    final starting = fake.start();
    fake.permission!.complete(false);
    await expectLater(starting, throwsStateError);
    expect(fake.events, ['permission']);

    fake.permission = null;
    await fake.start();
    expect(fake.capture.isRecording, isTrue);
    await fake.capture.stop();
  });

  test('dispose cancels pending permission and blocks later starts', () async {
    final fake = FakeRecorder()..permission = Completer<bool>();
    final starting = fake.start();
    await drainTasks();
    final disposing = fake.capture.dispose();
    fake.permission!.complete(true);
    await Future.wait([starting, disposing]);
    await fake.start();

    expect(fake.events, ['permission']);
    expect(fake.capture.isRecording, isFalse);
  });
}
