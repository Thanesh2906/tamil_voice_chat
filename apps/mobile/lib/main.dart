/// Jarvis mobile push-to-talk client (Phase 1 skeleton).
///
/// Phase 1 focuses on the smallest useful flow:
///   - user logs in
///   - holds a button to capture mic audio
///   - streams 16kHz mono PCM frames over the /voice/session WebSocket
///   - receives transcript / token / audio events and renders them
///
/// The full UI (project picker, source citations, monitoring charts,
/// barge-in controls, family account switcher) is layered on top of
/// the same WebSocket in later phases.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

void main() => runApp(const JarvisApp());

class JarvisApp extends StatelessWidget {
  const JarvisApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Jarvis',
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: Colors.indigo,
      ),
      home: const VoicePage(),
    );
  }
}

class VoicePage extends StatefulWidget {
  const VoicePage({super.key});

  @override
  State<VoicePage> createState() => _VoicePageState();
}

class _VoicePageState extends State<VoicePage> {
  final FlutterSoundRecorder _recorder = FlutterSoundRecorder();
  StreamSubscription<Food>? _recSub;
  WebSocketChannel? _channel;

  bool _ready = false;
  bool _talking = false;
  String _transcript = '';
  String _answer = '';
  String? _audioHex;

  @override
  void initState() {
    super.initState();
    _openSession();
  }

  Future<void> _openSession() async {
    final channel = WebSocketChannel.connect(
      Uri.parse('ws://10.0.2.2:8000/voice/session?token=dev'),
    );
    _channel = channel;
    channel.stream.listen((event) {
      final m = json.decode(event as String) as Map<String, dynamic>;
      final type = m['type'];
      setState(() {
        if (type == 'ready') _ready = true;
        if (type == 'transcript') _transcript = m['data']['text'] ?? _transcript;
        if (type == 'partial') _transcript = m['data']['text'] ?? _transcript;
        if (type == 'token') {
          _answer = (_answer + (m['data']['text'] ?? '').toString());
        }
        if (type == 'audio') _audioHex = m['data']['hex'];
        if (type == 'final') _audioHex = null;
      });
    });
  }

  Future<void> _startTalking() async {
    if (!_ready || _talking) return;
    await _recorder.openRecorder();
    await _recorder.startRecorder(
      toStream: _onPcm,
      codec: Codec.pcm16,
      sampleRate: 16000,
      numChannels: 1,
    );
    setState(() => _talking = true);
    _channel?.sink.add(json.encode({'type': 'start'}));
  }

  void _onPcm(Food food) {
    final Uint8List pcm = food.data ?? Uint8List(0);
    if (pcm.isEmpty) return;
    _channel?.sink.add(pcm);
  }

  Future<void> _stopTalking() async {
    if (!_talking) return;
    await _recorder.stopRecorder();
    await _recorder.closeRecorder();
    setState(() => _talking = false);
    _channel?.sink.add(json.encode({'type': 'stop'}));
  }

  @override
  void dispose() {
    _recSub?.cancel();
    _channel?.sink.close(ws_status.normalClosure);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Jarvis')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Transcript', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text(_transcript.isEmpty ? '—' : _transcript),
            const SizedBox(height: 24),
            Text('Answer', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Expanded(child: SingleChildScrollView(child: Text(_answer))),
            const SizedBox(height: 16),
            ElevatedButton.icon(
              onPressed: _ready ? (_talking ? _stopTalking : _startTalking) : null,
              icon: Icon(_talking ? Icons.stop : Icons.mic),
              label: Text(_talking ? 'Stop' : 'Hold to talk'),
            ),
          ],
        ),
      ),
    );
  }
}
