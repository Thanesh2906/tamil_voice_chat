# Flutter client

The Dart source implements login, secure refresh-token storage, microphone permission, PCM streaming and audio playback. Configure the API at build time:

```bash
flutter run --dart-define=JARVIS_API_URL=https://jarvis.example.com
```

Android/iOS permissions and build scaffolding are versioned here. Release builds require HTTPS/WSS; clear-text traffic is disabled. Supply the server at build time, for example `flutter build apk --dart-define=JARVIS_API_URL=https://jarvis.example.com`.
