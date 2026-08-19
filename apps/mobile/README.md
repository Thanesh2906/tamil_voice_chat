# Jarvis mobile (Phase 1)

Minimal Flutter push-to-talk client that opens a WebSocket to
`/voice/session`, streams 16-bit PCM at 16kHz while the user holds
the button, and renders transcript / token / audio events.

```bash
flutter pub get
flutter run -d <device>
```

## Notes

- Replace `10.0.2.2:8000` in `lib/main.dart` with your backend host
  (`localhost:8000` for desktop, your tunnel URL for phone).
- The Phase 1 dev token is hard-coded. Wire `/auth/login` before
  shipping to anyone outside your laptop.
- Microphone permission is declared by `flutter_sound`; ensure your
  `AndroidManifest.xml` and `Info.plist` include the mic + local
  network entries.
