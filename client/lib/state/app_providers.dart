import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api_client.dart';
import '../core/models/chunk.dart';
import '../core/models/claim.dart';
import '../core/models/manuscript.dart';
import '../core/models/outline.dart';
import '../core/models/perspective.dart';

// API Client Provider
final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient();
});

// Demo Mode Provider
class IsDemoModeNotifier extends Notifier<bool> {
  @override
  bool build() => true;
  void set(bool val) => state = val;
  void toggle() => state = !state;
}
final isDemoModeProvider = NotifierProvider<IsDemoModeNotifier, bool>(IsDemoModeNotifier.new);

// Connection Status Provider
class ConnectionStatusNotifier extends Notifier<ConnectionStatus> {
  @override
  ConnectionStatus build() {
    final isDemo = ref.watch(isDemoModeProvider);
    return isDemo ? ConnectionStatus.mockMode : ConnectionStatus.disconnected;
  }
  void set(ConnectionStatus val) => state = val;
}
final connectionStatusProvider =
    NotifierProvider<ConnectionStatusNotifier, ConnectionStatus>(ConnectionStatusNotifier.new);

// Latency & Token Count Providers
class LatencyMsNotifier extends Notifier<int> {
  @override
  int build() => 16;
  void set(int val) => state = val;
}
final latencyMsProvider = NotifierProvider<LatencyMsNotifier, int>(LatencyMsNotifier.new);

class TokenCountNotifier extends Notifier<int> {
  @override
  int build() => 0;
  void set(int val) => state = val;
  void increment(int count) => state += count;
}
final tokenCountProvider = NotifierProvider<TokenCountNotifier, int>(TokenCountNotifier.new);

// Outline Provider
class OutlineNotifier extends Notifier<List<OutlineNode>> {
  @override
  List<OutlineNode> build() => [];
  void set(List<OutlineNode> list) => state = list;
}
final outlineProvider = NotifierProvider<OutlineNotifier, List<OutlineNode>>(OutlineNotifier.new);

// Perspectives Provider
class PerspectivesNotifier extends Notifier<List<StormPerspective>> {
  @override
  List<StormPerspective> build() => [];
  void set(List<StormPerspective> list) => state = list;
  void toggle(String id, bool selected) {
    state = [
      for (final p in state)
        if (p.id == id) p.copyWith(isSelected: selected) else p,
    ];
  }
}
final perspectivesProvider =
    NotifierProvider<PerspectivesNotifier, List<StormPerspective>>(PerspectivesNotifier.new);

// Manuscript Provider
class ManuscriptNotifier extends Notifier<Manuscript> {
  @override
  Manuscript build() => Manuscript.empty();
  void set(Manuscript val) => state = val;
  void appendText(String delta) {
    final updatedContent = state.content + delta;
    final words = Manuscript.calculateWordCount(updatedContent);
    final readTime = Manuscript.calculateReadingTime(words);
    state = state.copyWith(
      content: updatedContent,
      wordCount: words,
      readingTimeMinutes: readTime,
    );
  }
  void setGroundingScore(double score) {
    state = state.copyWith(groundingScore: score);
  }
}
final manuscriptProvider =
    NotifierProvider<ManuscriptNotifier, Manuscript>(ManuscriptNotifier.new);

// Claims Provider
class ClaimsNotifier extends Notifier<List<Claim>> {
  @override
  List<Claim> build() => [];
  void set(List<Claim> list) => state = list;
  void add(Claim claim) => state = [...state, claim];
}
final claimsProvider = NotifierProvider<ClaimsNotifier, List<Claim>>(ClaimsNotifier.new);

// Chunks Provider
class ChunksNotifier extends Notifier<Map<String, SourceChunk>> {
  @override
  Map<String, SourceChunk> build() => {};
  void set(Map<String, SourceChunk> map) => state = map;
  void addAll(List<SourceChunk> chunks) {
    final map = <String, SourceChunk>{...state};
    for (final c in chunks) {
      map[c.chunkId] = c;
    }
    state = map;
  }
}
final chunksProvider =
    NotifierProvider<ChunksNotifier, Map<String, SourceChunk>>(ChunksNotifier.new);

// Selected Claim & Chunk ID Providers
class SelectedClaimNotifier extends Notifier<Claim?> {
  @override
  Claim? build() => null;
  void set(Claim? val) => state = val;
}
final selectedClaimProvider =
    NotifierProvider<SelectedClaimNotifier, Claim?>(SelectedClaimNotifier.new);

class SelectedChunkIdNotifier extends Notifier<String?> {
  @override
  String? build() => null;
  void set(String? val) => state = val;
}
final selectedChunkIdProvider =
    NotifierProvider<SelectedChunkIdNotifier, String?>(SelectedChunkIdNotifier.new);

// Streaming & Outline Generation Flags
class IsStreamingNotifier extends Notifier<bool> {
  @override
  bool build() => false;
  void set(bool val) => state = val;
}
final isStreamingProvider = NotifierProvider<IsStreamingNotifier, bool>(IsStreamingNotifier.new);

class IsGeneratingOutlineNotifier extends Notifier<bool> {
  @override
  bool build() => false;
  void set(bool val) => state = val;
}
final isGeneratingOutlineProvider =
    NotifierProvider<IsGeneratingOutlineNotifier, bool>(IsGeneratingOutlineNotifier.new);

/// Generation Orchestrator Controller
final generationControllerProvider = Provider<GenerationController>((ref) {
  return GenerationController(ref);
});

class GenerationController {
  final Ref _ref;
  StreamSubscription<SseEvent>? _subscription;

  GenerationController(this._ref);

  void toggleDemoMode() {
    _ref.read(isDemoModeProvider.notifier).toggle();
    final isDemo = _ref.read(isDemoModeProvider);
    _ref.read(connectionStatusProvider.notifier).set(
          isDemo ? ConnectionStatus.mockMode : ConnectionStatus.disconnected,
        );
  }

  void selectClaim(Claim claim) {
    _ref.read(selectedClaimProvider.notifier).set(claim);
    _ref.read(selectedChunkIdProvider.notifier).set(claim.chunkId);
  }

  void selectCitation(String chunkId, String? citationId) {
    _ref.read(selectedChunkIdProvider.notifier).set(chunkId);
    final claims = _ref.read(claimsProvider);
    for (final claim in claims) {
      if (claim.chunkId == chunkId || (citationId != null && claim.citationId == citationId)) {
        _ref.read(selectedClaimProvider.notifier).set(claim);
        break;
      }
    }
  }

  void togglePerspective(String id, bool selected) {
    _ref.read(perspectivesProvider.notifier).toggle(id, selected);
  }

  void clearAll() {
    _cancelStream();
    _ref.read(manuscriptProvider.notifier).set(Manuscript.empty());
    _ref.read(claimsProvider.notifier).set([]);
    _ref.read(chunksProvider.notifier).set({});
    _ref.read(outlineProvider.notifier).set([]);
    _ref.read(perspectivesProvider.notifier).set([]);
    _ref.read(selectedClaimProvider.notifier).set(null);
    _ref.read(selectedChunkIdProvider.notifier).set(null);
    _ref.read(tokenCountProvider.notifier).set(0);
    _ref.read(isStreamingProvider.notifier).set(false);
  }

  void generateOutlineOnly() async {
    _ref.read(isGeneratingOutlineProvider.notifier).set(true);
    await Future.delayed(const Duration(milliseconds: 600));

    final sampleOutline = [
      OutlineNode(
        id: 'sec-1',
        title: '1. Theoretical Formulations of Latent Dual-Encoders',
        sectionGoal: 'Establish bi-encoder dot product MIPS foundations and contrastive loss optimization.',
        level: 1,
        assignedChunkIds: ['chunk-001', 'chunk-004'],
        isCompleted: true,
      ),
      OutlineNode(
        id: 'sec-2',
        title: '2. The Zero-Shot Out-of-Domain Generalization Paradox',
        sectionGoal: 'Contrast empirical in-domain gains against lexical BM25 robustness under distribution shift.',
        level: 1,
        assignedChunkIds: ['chunk-002', 'chunk-003'],
        isCompleted: false,
        subsections: [
          OutlineNode(
            id: 'sec-2-1',
            title: '2.1 Lexical Guarantees vs Latent Proximity',
            sectionGoal: 'Formalize term frequency saturation versus dot product cosine metric spaces.',
            level: 2,
            assignedChunkIds: ['chunk-003'],
            isCompleted: false,
          ),
          OutlineNode(
            id: 'sec-2-2',
            title: '2.2 Multi-Vector Late Interaction Bridges',
            sectionGoal: 'Evaluate ColBERT token-level MaxSim indexing versus single vector compression.',
            level: 2,
            assignedChunkIds: ['chunk-004'],
            isCompleted: false,
          ),
        ],
      ),
      OutlineNode(
        id: 'sec-3',
        title: '3. Architectural Synthesis and Verification Mandates',
        sectionGoal: 'Conclude with NLI claim boundary verifications and provenance auditability.',
        level: 1,
        assignedChunkIds: ['chunk-001', 'chunk-002'],
        isCompleted: false,
      ),
    ];

    _ref.read(outlineProvider.notifier).set(sampleOutline);
    _ref.read(isGeneratingOutlineProvider.notifier).set(false);
  }

  void startOrToggleStreaming() {
    final isStreaming = _ref.read(isStreamingProvider);
    if (isStreaming) {
      _cancelStream();
      return;
    }

    final isDemo = _ref.read(isDemoModeProvider);
    final client = _ref.read(apiClientProvider);

    _ref.read(isStreamingProvider.notifier).set(true);
    _ref.read(connectionStatusProvider.notifier).set(
          isDemo ? ConnectionStatus.mockMode : ConnectionStatus.connecting,
        );

    final stream = isDemo
        ? client.streamDemoGeneration()
        : client.streamGeneration(query: 'Dense Retrieval Architectures');

    _subscription = stream.listen(
      (event) {
        if (event is PerspectivesEvent) {
          _ref.read(perspectivesProvider.notifier).set(event.perspectives);
        } else if (event is ChunksEvent) {
          _ref.read(chunksProvider.notifier).addAll(event.chunks);
        } else if (event is OutlineEvent) {
          _ref.read(outlineProvider.notifier).set(event.nodes);
        } else if (event is TokenDeltaEvent) {
          _ref.read(manuscriptProvider.notifier).appendText(event.text);
        } else if (event is ClaimVerifiedEvent) {
          _ref.read(claimsProvider.notifier).add(event.claim);
          if (_ref.read(selectedClaimProvider) == null) {
            _ref.read(selectedClaimProvider.notifier).set(event.claim);
            _ref.read(selectedChunkIdProvider.notifier).set(event.claim.chunkId);
          }
        } else if (event is StreamMetricsEvent) {
          _ref.read(tokenCountProvider.notifier).set(event.tokenCount);
          _ref.read(latencyMsProvider.notifier).set(event.latencyMs);
          _ref.read(manuscriptProvider.notifier).setGroundingScore(event.groundingScore);
        } else if (event is StreamCompletedEvent) {
          _ref.read(isStreamingProvider.notifier).set(false);
        }
      },
      onError: (err) {
        _ref.read(isStreamingProvider.notifier).set(false);
        _ref.read(connectionStatusProvider.notifier).set(ConnectionStatus.disconnected);
      },
      onDone: () {
        _ref.read(isStreamingProvider.notifier).set(false);
      },
    );
  }

  void _cancelStream() {
    _subscription?.cancel();
    _subscription = null;
    _ref.read(isStreamingProvider.notifier).set(false);
  }
}
