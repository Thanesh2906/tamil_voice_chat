# Flutter client

The Dart source implements login, secure refresh-token storage, microphone permission, PCM streaming and audio playback. Configure the API at build time:

```bash
flutter run --dart-define=JARVIS_API_URL=https://jarvis.example.com
```

Android/iOS permissions and build scaffolding are versioned here. Release builds require HTTPS/WSS; clear-text traffic is disabled. Supply the server at build time, for example `flutter build apk --dart-define=JARVIS_API_URL=https://jarvis.example.com`.

## Checks

```bash
flutter pub get
flutter analyze
flutter test
```

`test/widget_test.dart` exercises the actual `JarvisApp` entry point and is kept
in source control so `flutter create --platforms=android,ios .` does not generate
a counter-app test against a nonexistent `MyApp`. The voice-capture tests cover
control/PCM ordering, repeated taps, interrupted permission/startup, disconnects,
startup failure, and disposal with a fake recorder. Device verification is still
required for microphone permission, background interruption, and PCM playback on
both Android and iOS.
