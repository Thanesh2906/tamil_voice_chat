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

## Android build compatibility

Android 7.0 (API 24) or newer is required. The resolved `flutter_sound` 9.30.0
plugin requires API 24 for recording; the previous API 23 target cannot build
against it. The namespace and application ID remain `com.example.jarvis_mobile`.

CI pins Flutter 3.47.6, JDK 17, Android Gradle Plugin 8.11.1, Kotlin 2.2.21 and
Gradle 8.14.4. The Gradle distribution is verified against its official SHA-256
checksum. These meet Flutter 3.47.6's hard minimums while retaining the existing
Groovy build files and app configuration. CI generates missing platform resources,
then removes only the three untracked Kotlin DSL files added by `flutter create`;
it refuses to remove tracked files and checks that custom Android sources survive.

Sources: [Flutter dependency checks](https://github.com/flutter/flutter/blob/3.47.6/packages/flutter_tools/gradle/src/main/kotlin/DependencyVersionChecker.kt),
[Kotlin compatibility](https://kotlinlang.org/docs/gradle-configure-project.html),
[AGP compatibility](https://developer.android.com/build/releases/agp-8-11-0-release-notes),
[Gradle 8.14.4](https://docs.gradle.org/8.14.4/release-notes.html), and
[flutter_sound 9.30.0 package source](https://pub.dev/api/archives/flutter_sound-9.30.0.tar.gz).
