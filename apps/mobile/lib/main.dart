import 'dart:async';
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

const apiBase = String.fromEnvironment('JARVIS_API_URL', defaultValue: 'http://10.0.2.2:8000');
final wsBase = apiBase.replaceFirst(RegExp('^http'), 'ws');
const storage = FlutterSecureStorage();

void main() => runApp(const JarvisApp());

class JarvisApp extends StatelessWidget {
  const JarvisApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
      title: 'Jarvis', theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo),
      home: const LoginPage());
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
    Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => VoicePage(accessToken: token['access_token'] as String)));
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

class _VoicePageState extends State<VoicePage> {
  final recorder = FlutterSoundRecorder();
  final player = AudioPlayer();
  WebSocketChannel? channel;
  bool ready = false;
  bool talking = false;
  String transcript = '';
  String answer = '';
  final citations = <String>[];
  String accessToken = '';
  String? projectId;
  List<Map<String, dynamic>> projects = [];
  Timer? reconnectTimer;
  bool disposed = false;

  @override
  void initState() { super.initState(); accessToken = widget.accessToken; loadProjects(); openSession(); }

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
      setState(() { projects = values; projectId ??= values.isEmpty ? null : values.first['id'] as String; });
    }
  }

  Future<void> openSession() async {
    final socket = WebSocketChannel.connect(Uri.parse('$wsBase/voice/session'));
    channel = socket;
    socket.sink.add(jsonEncode({'type': 'auth', 'access_token': accessToken}));
    socket.stream.listen((event) async {
      final message = jsonDecode(event as String) as Map<String, dynamic>;
      final data = (message['data'] as Map<String, dynamic>?) ?? {};
      if (!mounted) return;
      if (message['type'] == 'audio') {
        await player.play(BytesSource(Uint8List.fromList(hexToBytes(data['audio'] as String? ?? ''))));
      }
      setState(() {
        if (message['type'] == 'authenticated') ready = true;
        if (message['type'] == 'transcript' || message['type'] == 'partial') transcript = data['text'] as String? ?? transcript;
        if (message['type'] == 'token') answer += data['text'] as String? ?? '';
        if (message['type'] == 'citation') citations.add('${data['file_path']}:${data['line_start']}-${data['line_end']}');
      });
    }, onDone: reconnect, onError: (_) => reconnect());
  }

  void reconnect() {
    if (disposed || !mounted) return;
    setState(() => ready = false);
    reconnectTimer?.cancel();
    reconnectTimer = Timer(const Duration(seconds: 2), () async {
      await refreshAccessToken();
      if (!disposed) openSession();
    });
  }

  Future<void> logout() async {
    disposed = true;
    reconnectTimer?.cancel();
    await storage.delete(key: 'refresh_token');
    await channel?.sink.close(ws_status.normalClosure);
    if (mounted) Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const LoginPage()), (_) => false);
  }

  List<int> hexToBytes(String hex) => [for (var i = 0; i < hex.length; i += 2) int.parse(hex.substring(i, i + 2), radix: 16)];

  Future<void> startTalking() async {
    if (!ready || talking || !await Permission.microphone.request().isGranted) return;
    await recorder.openRecorder();
    final controller = StreamController<Food>();
    controller.stream.listen((food) { if (food.data?.isNotEmpty ?? false) channel?.sink.add(food.data!); });
    await recorder.startRecorder(toStream: controller.sink, codec: Codec.pcm16, sampleRate: 16000, numChannels: 1);
    channel?.sink.add(jsonEncode({'type': 'start', 'request_id': DateTime.now().microsecondsSinceEpoch.toString(),
      'session_id': 'mobile', 'project_id': projectId, 'sample_rate': 16000, 'language': 'ta'}));
    setState(() { talking = true; answer = ''; citations.clear(); });
  }

  Future<void> stopTalking() async {
    if (!talking) return;
    await recorder.stopRecorder(); await recorder.closeRecorder();
    channel?.sink.add(jsonEncode({'type': 'stop'}));
    setState(() => talking = false);
  }

  @override
  void dispose() { disposed = true; reconnectTimer?.cancel(); channel?.sink.close(ws_status.normalClosure); recorder.closeRecorder(); player.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) => Scaffold(appBar: AppBar(title: const Text('Jarvis'), actions: [
    IconButton(onPressed: logout, tooltip: 'Sign out', icon: const Icon(Icons.logout)),
  ]), body: Padding(
    padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      DropdownButtonFormField<String>(value: projectId, decoration: const InputDecoration(labelText: 'Project'),
        items: projects.map((p) => DropdownMenuItem(value: p['id'] as String, child: Text(p['name'] as String))).toList(),
        onChanged: talking ? null : (value) => setState(() => projectId = value)),
      Text(ready ? 'Connected' : 'Reconnecting…', style: TextStyle(color: ready ? Colors.greenAccent : Colors.orangeAccent)),
      Text('Transcript', style: Theme.of(context).textTheme.titleMedium), Text(transcript.isEmpty ? '—' : transcript),
      const SizedBox(height: 20), Text('Answer', style: Theme.of(context).textTheme.titleMedium),
      Expanded(child: SingleChildScrollView(child: Text(answer))),
      if (citations.isNotEmpty) ...[Text('Sources', style: Theme.of(context).textTheme.titleMedium), ...citations.map(Text.new)],
      FilledButton.icon(onPressed: ready ? (talking ? stopTalking : startTalking) : null,
          icon: Icon(talking ? Icons.stop : Icons.mic), label: Text(talking ? 'Stop' : 'Talk')),
    ]),
  ));
}
