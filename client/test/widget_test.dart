import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:client/main.dart';

void main() {
  testWidgets('LMeditor studio loads with 3-pane layout', (WidgetTester tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const ProviderScope(child: LMeditorStudioApp()));
    await tester.pumpAndSettle();

    // Verify app title & branding
    expect(find.text('LMeditor'), findsOneWidget);
    expect(find.text('Grounded Expository Writing Studio'), findsOneWidget);

    // Verify 3-pane components
    expect(find.text('STORM (0)'), findsOneWidget);
    expect(find.textContaining('Outline'), findsWidgets);
    expect(find.text('NLI Verification Scorecard'), findsOneWidget);
    expect(find.text('Synthesize & Draft'), findsOneWidget);
  });
}
