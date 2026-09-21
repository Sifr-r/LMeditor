class Manuscript {
  final String title;
  final String currentSection;
  final String content;
  final int wordCount;
  final int readingTimeMinutes;
  final double groundingScore; // 0.0 to 1.0

  const Manuscript({
    required this.title,
    required this.currentSection,
    required this.content,
    required this.wordCount,
    required this.readingTimeMinutes,
    required this.groundingScore,
  });

  factory Manuscript.empty() {
    return const Manuscript(
      title: 'Dense Retrieval Architectures and Theoretical Foundations',
      currentSection: 'Introduction & Mathematical Pre-requisites',
      content: '',
      wordCount: 0,
      readingTimeMinutes: 0,
      groundingScore: 1.0,
    );
  }

  Manuscript copyWith({
    String? title,
    String? currentSection,
    String? content,
    int? wordCount,
    int? readingTimeMinutes,
    double? groundingScore,
  }) {
    return Manuscript(
      title: title ?? this.title,
      currentSection: currentSection ?? this.currentSection,
      content: content ?? this.content,
      wordCount: wordCount ?? this.wordCount,
      readingTimeMinutes: readingTimeMinutes ?? this.readingTimeMinutes,
      groundingScore: groundingScore ?? this.groundingScore,
    );
  }

  static int calculateWordCount(String text) {
    if (text.trim().isEmpty) return 0;
    // Clean citation tokens before counting words
    final cleaned = text.replaceAll(RegExp(r'\{\{cite:[^}]+\}\}|\[#[^\]]+\]'), '');
    final words = cleaned.trim().split(RegExp(r'\s+'));
    return words.where((w) => w.isNotEmpty).length;
  }

  static int calculateReadingTime(int words) {
    if (words <= 0) return 0;
    return (words / 200).ceil(); // ~200 WPM
  }
}
