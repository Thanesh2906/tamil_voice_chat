import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

import 'voice_capture.dart';
import 'voice_session.dart';

const apiBase = String.fromEnvironment('JARVIS_API_URL', defaultValue: 'https://jarvis.example.invalid');
final wsBase = apiBase.replaceFirst(RegExp('^http'), 'ws');
const storage = FlutterSecureStorage();

void main() => runApp(const JarvisApp());

class JarvisApp extends StatelessWidget {
  const JarvisApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
      title: 'Jarvis', theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo),
      home: const SessionGate());
}

class SessionGate extends StatefulWidget {
  const SessionGate({super.key});
  @override
  State<SessionGate> createState() => _SessionGateState();
}

class _SessionGateState extends State<SessionGate> {
  String? accessToken;
  bool loading = true;
  @override
  void initState() { super.initState(); restore(); }
  Future<void> restore() async {
    final refresh = await storage.read(key: 'refresh_token');
    if (refresh != null) {
      final response = await http.post(Uri.parse('$apiBase/auth/refresh'),
          headers: {'content-type': 'application/json'}, body: jsonEncode({'refresh_token': refresh}));
      if (response.statusCode == 200) {
        final token = jsonDecode(response.body) as Map<String, dynamic>;
        await storage.write(key: 'refresh_token', value: token['refresh_token'] as String);
        accessToken = token['access_token'] as String;
      } else {
        await storage.delete(key: 'refresh_token');
      }
    }
    loading = false;
    if (mounted) setState(() {});
  }
  @override
  Widget build(BuildContext context) {
    if (loading) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    return accessToken == null ? const LoginPage() : VoicePage(accessToken: accessToken!);
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final email = TextEditingController();
  final password = TextEditingController();
  String? error;

  Future<void> login() async {
    final response = await http.post(Uri.parse('$apiBase/auth/login'),
        headers: {'content-type': 'application/json'},
        body: jsonEncode({'email': email.text, 'password': password.text}));
    if (!mounted) return;
    if (response.statusCode != 200) { setState(() => error = 'Invalid credentials'); return; }
    final token = jsonDecode(response.body) as Map<String, dynamic>;
    await storage.write(key: 'refresh_token', value: token['refresh_token'] as String);
    if (!mounted) return;
    Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => VoicePage(accessToken: token['access_token'] as String)));
  }

  @override
  void dispose() {
    email.dispose();
    password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(body: Center(child: ConstrainedBox(
    constraints: const BoxConstraints(maxWidth: 420),
    child: Padding(padding: const EdgeInsets.all(24), child: Column(mainAxisSize: MainAxisSize.min, children: [
      Text('Jarvis', style: Theme.of(context).textTheme.headlineMedium),
      TextField(controller: email, keyboardType: TextInputType.emailAddress,
          autofillHints: const [AutofillHints.username], decoration: const InputDecoration(labelText: 'Email')),
      TextField(controller: password, obscureText: true,
          autofillHints: const [AutofillHints.password], decoration: const InputDecoration(labelText: 'Password')),
      if (error != null) Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
      const SizedBox(height: 16), FilledButton(onPressed: login, child: const Text('Sign in')),
    ])),
  )));
}

class VoicePage extends StatefulWidget {
  final String accessToken;
  const VoicePage({super.key, required this.accessToken});
  @override
  State<VoicePage> createState() => _VoicePageState();
}

class _VoicePageState extends State<VoicePage> with WidgetsBindingObserver {
  final recorder = FlutterSoundRecorder();
  final player = AudioPlayer();
  WebSocketChannel? channel;
  final voiceSession = VoiceSession();
  Timer? authenticationTimer;
  bool ready = false;
  bool talking = false;
  String transcript = '';
  String answer = '';
  final citations = <String>[];
  final audioQueue = Queue<Uint8List>();
  late final VoiceCapture capture;
  bool microphoneBusy = false;
  String? voiceError;
  StreamSubscription<void>? playbackSubscription;
  bool playing = false;
  String accessToken = '';
  String? projectId;
  List<Map<String, dynamic>> projects = [];
  String profileName = '';
  Timer? reconnectTimer;
  bool disposed = false;

  @override
  void initState() {
    super.initState();
    accessToken = widget.accessToken;
    WidgetsBinding.instance.addObserver(this);
    capture = VoiceCapture(
      requestPermission: () => Permission.microphone.request().isGranted,
      openRecorder: () async { await recorder.openRecorder(); },
      startRecorder: (sink) => recorder.startRecorder(
          toStream: sink, codec: Codec.pcm16, sampleRate: 16000, numChannels: 1),
      stopRecorder: () async { await recorder.stopRecorder(); },
      closeRecorder: () async { await recorder.closeRecorder(); },
    );
    playbackSubscription = player.onPlayerComplete.listen((_) {
      playing = false;
      if (!disposed) playNextAudio();
    });
    loadProfile();
    loadProjects();
    openSession();
  }

  Future<void> playNextAudio() async {
    if (disposed || playing || audioQueue.isEmpty) return;
    playing = true;
    await player.play(BytesSource(audioQueue.removeFirst()));
  }

  Future<bool> refreshAccessToken() async {
    final refresh = await storage.read(key: 'refresh_token');
    if (refresh == null) return false;
    final response = await http.post(Uri.parse('$apiBase/auth/refresh'),
        headers: {'content-type': 'application/json'}, body: jsonEncode({'refresh_token': refresh}));
    if (response.statusCode != 200) return false;
    final token = jsonDecode(response.body) as Map<String, dynamic>;
    accessToken = token['access_token'] as String;
    await storage.write(key: 'refresh_token', value: token['refresh_token'] as String);
    return true;
  }

  Future<void> loadProjects({bool retry = true}) async {
    var response = await http.get(Uri.parse('$apiBase/projects'), headers: {'authorization': 'Bearer $accessToken'});
    if (response.statusCode == 401 && retry && await refreshAccessToken()) return loadProjects(retry: false);
    if (response.statusCode == 200 && mounted) {
      final values = (jsonDecode(response.body) as List).cast<Map<String, dynamic>>();
      setState(() {
        projects = values;
        if (!microphoneBusy && !talking && voiceSession.requestId == null) {
          projectId ??= values.isEmpty ? null : values.first['id'] as String;
        }
      });
    }
  }

  Future<void> loadProfile({bool retry = true}) async {
    var response = await http.get(Uri.parse('$apiBase/me'), headers: {'authorization': 'Bearer $accessToken'});
    if (response.statusCode == 401 && retry && await refreshAccessToken()) return loadProfile(retry: false);
    if (response.statusCode == 200 && mounted) {
      final profile = jsonDecode(response.body) as Map<String, dynamic>;
      setState(() => profileName = profile['display_name'] as String? ?? 'Personal profile');
    }
  }

  Future<void> openSession() async {
    if (disposed) return;
    reconnectTimer?.cancel();
    authenticationTimer?.cancel();
    final previous = channel;
    channel = null;
    voiceSession.newConnection();
    previous?.sink.close(ws_status.normalClosure);
    if (mounted) setState(() => ready = false);
    final socket = WebSocketChannel.connect(Uri.parse('$wsBase/voice/session'));
    channel = socket;
    authenticationTimer = Timer(const Duration(seconds: 10), () {
      if (!disposed && channel == socket && !voiceSession.authenticated) {
        setState(() => voiceError = 'Voice authentication timed out. Reconnecting…');
        reconnect();
      }
    });
    socket.sink.add(jsonEncode({'type': 'auth', 'access_token': accessToken}));
    socket.stream.listen((event) async {
      if (disposed || !mounted || channel != socket) return;
      try {
        final message = jsonDecode(event as String) as Map<String, dynamic>;
        final data = (message['data'] as Map<String, dynamic>?) ?? {};
        if (message['type'] == 'authenticated') {
          authenticationTimer?.cancel();
          voiceSession.authenticated = true;
          setState(() { ready = true; voiceError = null; });
          return;
        }
        if (!voiceSession.matches(message)) return;
        if (message['type'] == 'ready') {
          voiceSession.acknowledge(message);
          return;
        }
        if (message['type'] == 'error') {
          setState(() => voiceError = data['detail'] as String? ?? 'The voice request failed. Please try again.');
          await cancelResponse();
          return;
        }
        if (message['type'] == 'barge_in') {
          await cancelResponse();
          return;
        }
        if (message['type'] == 'audio') {
          audioQueue.add(Uint8List.fromList(hexToBytes(data['audio'] as String? ?? '')));
          await playNextAudio();
        }
        if (disposed || !mounted || channel != socket || !voiceSession.matches(message)) return;
        setState(() {
          if (message['type'] == 'transcript' || message['type'] == 'partial') transcript = data['text'] as String? ?? transcript;
          if (message['type'] == 'token') answer += data['text'] as String? ?? '';
          if (message['type'] == 'citation') citations.add('${data['file_path']}:${data['line_start']}-${data['line_end']}');
        });
      } catch (_) {
        if (!disposed && mounted && channel == socket) {
          setState(() => voiceError = 'Could not read the voice response. Please try again.');
          await cancelResponse();
        }
      }
    }, onDone: () { if (channel == socket) reconnect(); },
        onError: (Object _) { if (channel == socket) reconnect(); });
  }

  void reconnect() {
    if (disposed || !mounted) return;
    authenticationTimer?.cancel();
    final previous = channel;
    channel = null;
    voiceSession.disconnect();
    previous?.sink.close(ws_status.normalClosure);
    setState(() { ready = false; talking = false; });
    unawaited(cancelResponse());
    reconnectTimer?.cancel();
    reconnectTimer = Timer(const Duration(seconds: 2), () async {
      try {
        if (!await refreshAccessToken()) {
          if (mounted && !disposed) setState(() => voiceError = 'Your session expired. Sign out and sign in again.');
          return;
        }
        if (!disposed) await openSession();
      } catch (_) {
        if (mounted && !disposed) {
          setState(() => voiceError = 'Voice connection failed. Reconnecting…');
          reconnect();
        }
      }
    });
  }

  Future<void> cancelResponse() async {
    final socket = channel;
    final requestId = voiceSession.requestId;
    final sessionId = voiceSession.sessionId;
    final stopping = stopTalking(cancelled: true);
    if (socket != null && requestId != null && voiceSession.authenticated) {
      try {
        socket.sink.add(jsonEncode({'type': 'barge_in', 'request_id': requestId, 'session_id': sessionId}));
      } catch (_) { /* A closed socket must not prevent local cancellation. */ }
    }
    voiceSession.invalidateTurn();
    audioQueue.clear();
    playing = false;
    try { await player.stop(); } catch (_) { /* Capture cleanup must still finish. */ }
    await stopping;
  }

  Future<void> selectProject(String? value) async {
    if (value == projectId || microphoneBusy || talking) return;
    final stopping = cancelResponse();
    voiceSession.selectProject(value);
    if (mounted && !disposed) setState(() { projectId = value; answer = ''; transcript = ''; citations.clear(); });
    await stopping;
  }

  Future<void> logout() async {
    disposed = true;
    reconnectTimer?.cancel();
    authenticationTimer?.cancel();
    await cancelResponse();
    await storage.delete(key: 'refresh_token');
    await channel?.sink.close(ws_status.normalClosure);
    if (mounted) Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const LoginPage()), (_) => false);
  }

  List<int> hexToBytes(String hex) => [for (var i = 0; i < hex.length; i += 2) int.parse(hex.substring(i, i + 2), radix: 16)];

  Future<void> startTalking() async {
    final socket = channel;
    if (disposed || !ready || talking || microphoneBusy || socket == null || !voiceSession.authenticated) return;
    final selectedProject = projectId;
    final requestId = voiceSession.beginTurn(selectedProject);
    final sessionId = voiceSession.sessionId;
    setState(() { microphoneBusy = true; voiceError = null; });
    try {
      audioQueue.clear();
      playing = false;
      await player.stop();
      // Cancellation can happen while playback is stopping.
      if (disposed || channel != socket || voiceSession.requestId != requestId) return;
      await capture.start(
        canSend: () => !disposed && ready && channel == socket && voiceSession.requestId == requestId,
        sendStart: () {
          socket.sink.add(jsonEncode({
            'type': 'start', 'request_id': requestId, 'session_id': sessionId,
            'project_id': selectedProject, 'sample_rate': 16000, 'language': 'ta',
          }));
          if (mounted && !disposed) {
            setState(() { answer = ''; transcript = ''; citations.clear(); });
          }
        },
        waitForReady: () => voiceSession.requestId == requestId
            ? voiceSession.waitForReady() : Future<bool>.value(false),
        cancelPendingStart: () {
          if (voiceSession.requestId == requestId) voiceSession.cancelPendingStart();
        },
        sendAudio: (bytes) {
          if (voiceSession.recordingReady && voiceSession.requestId == requestId) socket.sink.add(bytes);
        },
        sendEnd: (cancelled) => socket.sink.add(jsonEncode({
          'type': cancelled ? 'barge_in' : 'stop', 'request_id': requestId, 'session_id': sessionId,
        })),
      );
    } catch (_) {
      if (mounted && !disposed && voiceSession.requestId == requestId) {
        setState(() => voiceError ??= 'Could not start voice recording. Check microphone permission and server readiness, then try again.');
      }
    } finally {
      if (mounted && !disposed && voiceSession.requestId == requestId) {
        setState(() { microphoneBusy = false; talking = capture.isRecording; });
      }
    }
  }

  Future<void> stopTalking({bool cancelled = false}) async {
    if (mounted && !disposed) setState(() => microphoneBusy = true);
    try {
      await capture.stop(cancelled: cancelled);
    } catch (_) {
      if (mounted && !disposed) {
        setState(() => voiceError = 'Could not close the microphone. Try again.');
      }
    } finally {
      if (mounted && !disposed) {
        setState(() { microphoneBusy = false; talking = false; });
      }
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // A permission sheet also causes inactive; do not cancel its pending prompt.
    if (state == AppLifecycleState.paused || state == AppLifecycleState.detached) {
      unawaited(cancelResponse());
    }
  }

  @override
  void dispose() {
    disposed = true;
    WidgetsBinding.instance.removeObserver(this);
    reconnectTimer?.cancel();
    authenticationTimer?.cancel();
    voiceSession.disconnect();
    playbackSubscription?.cancel();
    unawaited(capture.dispose().catchError((Object _) {}));
    channel?.sink.close(ws_status.normalClosure);
    player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(appBar: AppBar(title: const Text('Jarvis'), actions: [
    IconButton(onPressed: ready ? cancelResponse : null,
        tooltip: 'Cancel response', icon: const Icon(Icons.cancel_outlined)),
    IconButton(onPressed: logout, tooltip: 'Sign out', icon: const Icon(Icons.logout)),
  ]), body: Padding(
    padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      ListTile(contentPadding: EdgeInsets.zero, leading: const Icon(Icons.family_restroom),
        title: Text(profileName.isEmpty ? 'Personal / family profile' : profileName),
        subtitle: const Text('Projects and sources remain permission-isolated')),
      InputDecorator(
        decoration: const InputDecoration(labelText: 'Project'),
        child: DropdownButtonHideUnderline(child: DropdownButton<String>(
          value: projectId, isExpanded: true, isDense: true,
          items: projects.map((p) => DropdownMenuItem(value: p['id'] as String, child: Text(p['name'] as String))).toList(),
          onChanged: talking || microphoneBusy ? null : selectProject,
        )),
      ),
      Text(ready ? 'Connected' : 'Reconnecting…', style: TextStyle(color: ready ? Colors.greenAccent : Colors.orangeAccent)),
      Text('Transcript', style: Theme.of(context).textTheme.titleMedium), Text(transcript.isEmpty ? '—' : transcript),
      const SizedBox(height: 20), Text('Answer', style: Theme.of(context).textTheme.titleMedium),
      Expanded(child: SingleChildScrollView(child: Text(answer))),
      if (citations.isNotEmpty) ...[Text('Sources', style: Theme.of(context).textTheme.titleMedium), ...citations.map(Text.new)],
      if (voiceError != null) Text(voiceError!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
      FilledButton.icon(onPressed: ready && !microphoneBusy ? (talking ? () => stopTalking() : startTalking) : null,
          icon: Icon(talking ? Icons.stop : Icons.mic), label: Text(talking ? 'Stop' : 'Talk')),
    ]),
  ));
}
