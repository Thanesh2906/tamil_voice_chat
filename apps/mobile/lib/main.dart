import 'package:flutter/material.dart';

void main() => runApp(const JarvisApp());

class JarvisApp extends StatelessWidget {
  const JarvisApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'JARVIS', theme: ThemeData.dark(useMaterial3: true),
    home: const Scaffold(body: Center(child: Text('JARVIS Tamil Voice\nConfigure API login to continue', textAlign: TextAlign.center))),
  );
}

