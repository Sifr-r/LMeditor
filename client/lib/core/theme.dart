import 'package:flutter/material.dart';

class AppColors {
  static const Color verifiedGreen = Color(0xFF10B981);
  static const Color contradictionRed = Color(0xFFEF4444);
  static const Color neutralYellow = Color(0xFFF59E0B);
  static const Color ungroundedGray = Color(0xFF6B7280);

  static const Color background = Color(0xFF0B0F17);
  static const Color surface = Color(0xFF111827);
  static const Color card = Color(0xFF1A2234);
  static const Color hover = Color(0xFF243048);
  static const Color border = Color(0xFF2A364F);
  static const Color borderSubtle = Color(0xFF1E293B);

  static const Color primary = Color(0xFF6366F1);
  static const Color primaryLight = Color(0xFF818CF8);
  static const Color accentCyan = Color(0xFF38BDF8);

  static const Color textPrimary = Color(0xFFF8FAFC);
  static const Color textSecondary = Color(0xFF94A3B8);
  static const Color textMuted = Color(0xFF64748B);
}

class StudioTheme {
  static const Color background = AppColors.background;
  static const Color surfacePane = AppColors.surface;
  static const Color surfaceCard = AppColors.card;
  static const Color surfaceHover = AppColors.hover;
  static const Color border = AppColors.border;
  static const Color borderSubtle = AppColors.borderSubtle;

  static const Color primary = AppColors.primary;
  static const Color primaryLight = AppColors.primaryLight;
  static const Color accentCyan = AppColors.accentCyan;

  // NLI Entailment Colors
  static const Color verifiedEmerald = AppColors.verifiedGreen;
  static const Color contradictionCrimson = AppColors.contradictionRed;
  static const Color neutralAmber = AppColors.neutralYellow;
  static const Color ungroundedGray = AppColors.ungroundedGray;
  static const Color ungroundedCrimson = AppColors.contradictionRed;

  static const Color textPrimary = AppColors.textPrimary;
  static const Color textSecondary = AppColors.textSecondary;
  static const Color textMuted = AppColors.textMuted;

  static const TextStyle monoSmall = TextStyle(
    fontFamily: 'monospace',
    fontSize: 11.5,
    letterSpacing: 0.2,
  );

  static const TextStyle monoBadge = TextStyle(
    fontFamily: 'monospace',
    fontSize: 11.0,
    fontWeight: FontWeight.w700,
    letterSpacing: 0.4,
  );

  static ThemeData get darkTheme => LMeditorTheme.darkTheme;
}

class LMeditorTheme {
  static TextTheme createExpositoryTextTheme() {
    return const TextTheme(
      headlineLarge: TextStyle(
        fontFamily: 'Merriweather',
        fontFamilyFallback: ['Georgia', 'serif'],
        fontSize: 32,
        fontWeight: FontWeight.w700,
        height: 1.6,
        color: AppColors.textPrimary,
      ),
      headlineMedium: TextStyle(
        fontFamily: 'Merriweather',
        fontFamilyFallback: ['Georgia', 'serif'],
        fontSize: 26,
        fontWeight: FontWeight.w700,
        height: 1.6,
        color: AppColors.textPrimary,
      ),
      headlineSmall: TextStyle(
        fontFamily: 'Merriweather',
        fontFamilyFallback: ['Georgia', 'serif'],
        fontSize: 22,
        fontWeight: FontWeight.w600,
        height: 1.6,
        color: AppColors.textPrimary,
      ),
      titleLarge: TextStyle(
        fontFamily: 'Georgia',
        fontFamilyFallback: ['Merriweather', 'serif'],
        fontSize: 20,
        fontWeight: FontWeight.w600,
        height: 1.6,
        color: AppColors.textPrimary,
      ),
      titleMedium: TextStyle(
        fontFamily: 'Georgia',
        fontFamilyFallback: ['Merriweather', 'serif'],
        fontSize: 18,
        fontWeight: FontWeight.w500,
        height: 1.6,
        color: AppColors.textPrimary,
      ),
      titleSmall: TextStyle(
        fontFamily: 'Georgia',
        fontFamilyFallback: ['Merriweather', 'serif'],
        fontSize: 16,
        fontWeight: FontWeight.w500,
        height: 1.6,
        color: AppColors.textSecondary,
      ),
      bodyLarge: TextStyle(
        fontFamily: 'Georgia',
        fontFamilyFallback: ['Merriweather', 'serif'],
        fontSize: 16,
        height: 1.6,
        color: Color(0xFFE2E8F0),
      ),
      bodyMedium: TextStyle(
        fontFamily: 'Georgia',
        fontFamilyFallback: ['Merriweather', 'serif'],
        fontSize: 14,
        height: 1.6,
        color: AppColors.textPrimary,
      ),
      bodySmall: TextStyle(
        fontFamily: 'Georgia',
        fontFamilyFallback: ['Merriweather', 'serif'],
        fontSize: 12,
        height: 1.6,
        color: AppColors.textSecondary,
      ),
    );
  }

  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: AppColors.background,
      colorScheme: const ColorScheme.dark(
        primary: AppColors.primary,
        secondary: AppColors.accentCyan,
        surface: AppColors.surface,
        onSurface: AppColors.textPrimary,
        error: AppColors.contradictionRed,
      ),
      cardTheme: CardThemeData(
        color: AppColors.card,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: const BorderSide(color: AppColors.border, width: 1),
        ),
      ),
      scrollbarTheme: ScrollbarThemeData(
        thickness: WidgetStateProperty.all(8.0),
        radius: const Radius.circular(4.0),
        thumbVisibility: WidgetStateProperty.all(true),
      ),
      dividerTheme: const DividerThemeData(
        color: AppColors.borderSubtle,
        thickness: 1,
        space: 1,
      ),
      textTheme: createExpositoryTextTheme(),
    );
  }
}
