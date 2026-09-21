import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/api_client.dart';
import 'core/theme.dart';
import 'features/discovery/outline_tree_view.dart';
import 'features/discovery/perspective_card.dart';
import 'features/editor/editor_view.dart';
import 'features/verification/verification_panel.dart';
import 'state/app_providers.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: LMeditorStudioApp()));
}

class LMeditorStudioApp extends StatelessWidget {
  const LMeditorStudioApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LMeditor - Grounded Expository Writing Studio',
      debugShowCheckedModeBanner: false,
      theme: StudioTheme.darkTheme,
      home: const StudioMainView(),
    );
  }
}

class StudioMainView extends ConsumerStatefulWidget {
  const StudioMainView({super.key});

  @override
  ConsumerState<StudioMainView> createState() => _StudioMainViewState();
}

class _StudioMainViewState extends ConsumerState<StudioMainView> with SingleTickerProviderStateMixin {
  late TabController _leftTabController;

  @override
  void initState() {
    super.initState();
    _leftTabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _leftTabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final outline = ref.watch(outlineProvider);
    final perspectives = ref.watch(perspectivesProvider);
    final manuscript = ref.watch(manuscriptProvider);
    final claims = ref.watch(claimsProvider);
    final chunks = ref.watch(chunksProvider);
    final selectedClaim = ref.watch(selectedClaimProvider);
    final selectedChunkId = ref.watch(selectedChunkIdProvider);
    final isStreaming = ref.watch(isStreamingProvider);
    final isGeneratingOutline = ref.watch(isGeneratingOutlineProvider);
    final isDemoMode = ref.watch(isDemoModeProvider);
    final connectionStatus = ref.watch(connectionStatusProvider);
    final latencyMs = ref.watch(latencyMsProvider);
    final tokenCount = ref.watch(tokenCountProvider);

    final controller = ref.read(generationControllerProvider);

    return Scaffold(
      appBar: _buildStudioAppBar(isStreaming, isDemoMode, controller),
      body: Column(
        children: [
          Expanded(
            child: Row(
              children: [
                // LEFT PANE: STORM Perspectives & Outline Tree View (~300px)
                SizedBox(
                  width: 310,
                  child: _buildLeftPane(
                    perspectives: perspectives,
                    outline: outline,
                    controller: controller,
                    isGeneratingOutline: isGeneratingOutline,
                  ),
                ),
                const VerticalDivider(color: StudioTheme.border, width: 1),

                // CENTER PANE: Manuscript Rich Editor (Expanded)
                Expanded(
                  child: EditorView(
                    manuscript: manuscript,
                    claims: claims,
                    selectedChunkId: selectedChunkId,
                    selectedClaimId: selectedClaim?.claimId,
                    isStreaming: isStreaming,
                    onToggleStream: () => controller.startOrToggleStreaming(),
                    onClear: () => controller.clearAll(),
                    onCitationTap: (chunkId, citationId) {
                      controller.selectCitation(chunkId, citationId);
                    },
                  ),
                ),
                const VerticalDivider(color: StudioTheme.border, width: 1),

                // RIGHT PANE: NLI Verification Scorecard & Ground Truth Source Inspector (~380px)
                SizedBox(
                  width: 380,
                  child: VerificationPanel(
                    claims: claims,
                    chunks: chunks,
                    selectedClaimId: selectedClaim?.claimId,
                    onSelectClaim: (claim) {
                      controller.selectClaim(claim);
                    },
                  ),
                ),
              ],
            ),
          ),
          // BOTTOM STATUS BAR
          _buildStatusBar(
            connectionStatus: connectionStatus,
            isDemoMode: isDemoMode,
            tokenCount: tokenCount,
            groundingScore: manuscript.groundingScore,
            latencyMs: latencyMs,
            controller: controller,
          ),
        ],
      ),
    );
  }

  PreferredSizeWidget _buildStudioAppBar(
    bool isStreaming,
    bool isDemoMode,
    GenerationController controller,
  ) {
    return AppBar(
      backgroundColor: StudioTheme.surfacePane,
      elevation: 0,
      titleSpacing: 16,
      title: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: StudioTheme.primary.withValues(alpha: 0.2),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: StudioTheme.primaryLight.withValues(alpha: 0.5)),
            ),
            child: const Icon(Icons.auto_stories_rounded, size: 18, color: StudioTheme.primaryLight),
          ),
          const SizedBox(width: 10),
          const Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'LMeditor',
                style: TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w800,
                  letterSpacing: -0.2,
                  color: StudioTheme.textPrimary,
                ),
              ),
              Text(
                'Grounded Expository Writing Studio',
                style: TextStyle(
                  fontSize: 10.5,
                  fontWeight: FontWeight.w500,
                  color: StudioTheme.textSecondary,
                ),
              ),
            ],
          ),
        ],
      ),
      actions: [
        // Demo / Live SSE Mode Indicator Chip
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: isDemoMode
                ? StudioTheme.neutralAmber.withValues(alpha: 0.15)
                : StudioTheme.verifiedEmerald.withValues(alpha: 0.15),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: isDemoMode ? StudioTheme.neutralAmber : StudioTheme.verifiedEmerald,
              width: 1,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                isDemoMode ? Icons.science_outlined : Icons.sensors_rounded,
                size: 13,
                color: isDemoMode ? StudioTheme.neutralAmber : StudioTheme.verifiedEmerald,
              ),
              const SizedBox(width: 5),
              Text(
                isDemoMode ? 'Mock SSE Demo' : 'Live Gateway SSE',
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  color: isDemoMode ? StudioTheme.neutralAmber : StudioTheme.verifiedEmerald,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        // Run / Stream Generation Button
        FilledButton.icon(
          onPressed: () => controller.startOrToggleStreaming(),
          icon: Icon(
            isStreaming ? Icons.stop_rounded : Icons.auto_awesome_rounded,
            size: 16,
          ),
          label: Text(isStreaming ? 'Halt Generation' : 'Synthesize & Draft'),
          style: FilledButton.styleFrom(
            backgroundColor: isStreaming ? StudioTheme.contradictionCrimson : StudioTheme.primary,
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
            textStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
          ),
        ),
        const SizedBox(width: 12),
      ],
      bottom: const PreferredSize(
        preferredSize: Size.fromHeight(1),
        child: Divider(color: StudioTheme.border, height: 1),
      ),
    );
  }

  Widget _buildLeftPane({
    required List<dynamic> perspectives,
    required List<dynamic> outline,
    required GenerationController controller,
    required bool isGeneratingOutline,
  }) {
    return Container(
      color: StudioTheme.background,
      child: Column(
        children: [
          Container(
            color: StudioTheme.surfacePane,
            child: TabBar(
              controller: _leftTabController,
              isScrollable: true,
              tabAlignment: TabAlignment.start,
              indicatorColor: StudioTheme.primaryLight,
              indicatorWeight: 2.5,
              labelColor: StudioTheme.textPrimary,
              unselectedLabelColor: StudioTheme.textMuted,
              labelStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
              tabs: [
                Tab(
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.people_alt_outlined, size: 14),
                      const SizedBox(width: 6),
                      Text('STORM (${perspectives.length})'),
                    ],
                  ),
                ),
                Tab(
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.account_tree_outlined, size: 14),
                      const SizedBox(width: 6),
                      Text('Outline (${outline.length})'),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const Divider(color: StudioTheme.borderSubtle, height: 1),
          Expanded(
            child: TabBarView(
              controller: _leftTabController,
              children: [
                // STORM Perspectives Tab
                _buildPerspectivesList(perspectives, controller),
                // Outline Tree View Tab
                OutlineTreeView(
                  nodes: outline.cast(),
                  onGenerateOutline: () => controller.generateOutlineOnly(),
                  isGeneratingOutline: isGeneratingOutline,
                  onNodeSelected: (node) {
                    // Node clicked: highlight or filter
                  },
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPerspectivesList(List<dynamic> perspectives, GenerationController controller) {
    if (perspectives.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.psychology_outlined, size: 40, color: StudioTheme.textMuted),
              const SizedBox(height: 10),
              const Text(
                'No Personas Discovered',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: StudioTheme.textSecondary),
              ),
              const SizedBox(height: 4),
              const Text(
                'Click "Synthesize & Draft" to discover diverse STORM research personas.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 11.5, color: StudioTheme.textMuted, height: 1.4),
              ),
            ],
          ),
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: perspectives.length,
      itemBuilder: (context, index) {
        final perspective = perspectives[index];
        return PerspectiveCard(
          perspective: perspective,
          onToggleSelected: (selected) {
            controller.togglePerspective(perspective.id, selected);
          },
        );
      },
    );
  }

  Widget _buildStatusBar({
    required ConnectionStatus connectionStatus,
    required bool isDemoMode,
    required int tokenCount,
    required double groundingScore,
    required int latencyMs,
    required GenerationController controller,
  }) {
    final statusColor = connectionStatus == ConnectionStatus.connected ||
            connectionStatus == ConnectionStatus.mockMode
        ? StudioTheme.verifiedEmerald
        : (connectionStatus == ConnectionStatus.connecting
            ? StudioTheme.neutralAmber
            : StudioTheme.contradictionCrimson);

    final groundingPercent = (groundingScore * 100).toStringAsFixed(1);

    return Container(
      height: 32,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      decoration: const BoxDecoration(
        color: StudioTheme.surfacePane,
        border: Border(top: BorderSide(color: StudioTheme.border, width: 1)),
      ),
      child: Row(
        children: [
          // Connection Status
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: statusColor,
              boxShadow: [
                BoxShadow(
                  color: statusColor.withValues(alpha: 0.5),
                  blurRadius: 4,
                  spreadRadius: 1,
                ),
              ],
            ),
          ),
          const SizedBox(width: 7),
          Text(
            connectionStatus.displayName,
            style: TextStyle(
              fontFamily: 'monospace',
              fontSize: 11,
              fontWeight: FontWeight.w700,
              color: statusColor,
              letterSpacing: 0.4,
            ),
          ),
          const SizedBox(width: 14),
          const VerticalDivider(color: StudioTheme.borderSubtle, indent: 8, endIndent: 8),
          const SizedBox(width: 14),

          // Demo Mode Toggle Button
          InkWell(
            onTap: () => controller.toggleDemoMode(),
            borderRadius: BorderRadius.circular(4),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
              child: Row(
                children: [
                  Icon(
                    isDemoMode ? Icons.toggle_on_rounded : Icons.toggle_off_rounded,
                    size: 18,
                    color: isDemoMode ? StudioTheme.primaryLight : StudioTheme.textMuted,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    isDemoMode ? 'Mock Generator Active' : 'Live Gateway Active',
                    style: TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 10.5,
                      color: isDemoMode ? StudioTheme.primaryLight : StudioTheme.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
          ),

          const Spacer(),

          // Token count
          const Icon(Icons.token_rounded, size: 12, color: StudioTheme.textMuted),
          const SizedBox(width: 4),
          Text(
            '$tokenCount tokens',
            style: const TextStyle(
              fontFamily: 'monospace',
              fontSize: 11,
              color: StudioTheme.textSecondary,
            ),
          ),
          const SizedBox(width: 14),
          const VerticalDivider(color: StudioTheme.borderSubtle, indent: 8, endIndent: 8),
          const SizedBox(width: 14),

          // Grounding Score
          const Icon(Icons.shield_outlined, size: 12, color: StudioTheme.verifiedEmerald),
          const SizedBox(width: 4),
          Text(
            'Grounding: $groundingPercent%',
            style: const TextStyle(
              fontFamily: 'monospace',
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: StudioTheme.verifiedEmerald,
            ),
          ),
          const SizedBox(width: 14),
          const VerticalDivider(color: StudioTheme.borderSubtle, indent: 8, endIndent: 8),
          const SizedBox(width: 14),

          // Latency
          const Icon(Icons.speed_rounded, size: 12, color: StudioTheme.textMuted),
          const SizedBox(width: 4),
          Text(
            '${latencyMs}ms',
            style: const TextStyle(
              fontFamily: 'monospace',
              fontSize: 11,
              color: StudioTheme.textMuted,
            ),
          ),
        ],
      ),
    );
  }
}
