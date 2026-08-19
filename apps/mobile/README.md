# Flutter client

The Dart source implements login, secure refresh-token storage, microphone permission, PCM streaming and audio playback. Configure the API at build time:

```bash
flutter run --dart-define=JARVIS_API_URL=https://jarvis.example.com
```

Run `flutter create . --platforms=android,ios` in this directory with a supported Flutter SDK to generate/refresh complete platform scaffolding, then preserve the supplied microphone permissions in AndroidManifest.xml and Info.plist. Release builds require HTTPS/WSS; clear-text traffic is disabled.
