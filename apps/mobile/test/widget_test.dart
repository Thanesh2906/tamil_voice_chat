import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/main.dart';

void main() {
  testWidgets('Jarvis opens sign-in when no refresh token is stored', (tester) async {
    FlutterSecureStorage.setMockInitialValues({});

    await tester.pumpWidget(const JarvisApp());
    await tester.pumpAndSettle();

    expect(find.text('Jarvis'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Email'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'Password'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });
}
