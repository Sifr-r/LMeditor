import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../models/source.dart';
import '../../state/app_providers.dart';

/// Riverpod-managed list of ingested [Source]s.
///
/// Populated by [SourcePanel] when a user uploads a file or (in a future
/// revision) pastes a URL. The library sidebar in [SourcePanel.build] reads
/// from this provider to render the per-kind icon, title, and bibliographic
/// subtitle for each source.
class SourcesNotifier extends Notifier<List<Source>> {
  @override
  List<Source> build() => const [];

  void add(Source source) => state = [...state, source];

  void set(List<Source> sources) => state = sources;
}

final sourcesProvider =
    NotifierProvider<SourcesNotifier, List<Source>>(SourcesNotifier.new);

/// Library sidebar that lets the user add a source artifact (file or URL)
/// and view the list of already-ingested sources.
///
/// File uploads use `package:file_picker`'s platform picker; the chosen file
/// is forwarded to [LMeditorApiClient.uploadSource] (see
/// `client/lib/core/api_client.dart`) and the resulting [Source] is appended
/// to [sourcesProvider].
///
/// URL ingestion is intentionally a no-op SnackBar in this plan — the
/// `POST /api/v1/sources/url` endpoint and its Dart wiring are out of scope
/// for the source-ingestion plan that ships [SourcePanel].
class SourcePanel extends ConsumerStatefulWidget {
  const SourcePanel({super.key});

  @override
  ConsumerState<SourcePanel> createState() => _SourcePanelState();
}

class _SourcePanelState extends ConsumerState<SourcePanel> {
  final _urlController = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _urlController.dispose();
    super.dispose();
  }

  Future<void> _pickAndUpload() async {
    setState(() => _busy = true);
    try {
      final picked = await FilePicker.platform.pickFiles(withData: true);
      if (picked == null || picked.files.isEmpty) return;
      final f = picked.files.first;
      final bytes = f.bytes;
      if (bytes == null) return;
      final api = ref.read(apiClientProvider);
      final result = await api.uploadSource(
        sourceId: 'src_${DateTime.now().microsecondsSinceEpoch}',
        title: f.name,
        filename: f.name,
        bytes: Uint8List.fromList(bytes),
      );
      ref.read(sourcesProvider.notifier).add(result.source);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _addUrl() async {
    final url = _urlController.text.trim();
    if (url.isEmpty) return;
    setState(() => _busy = true);
    try {
      // For URL-only ingestion, the server expects a JSON body with kind=url.
      // Out of scope for this plan: implement /sources/url separately.
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('URL ingestion: POST $url — not yet wired in this plan')),
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final sources = ref.watch(sourcesProvider);
    return Column(
      children: [
        Row(
          children: [
            Expanded(
              child: ElevatedButton.icon(
                onPressed: _busy ? null : _pickAndUpload,
                icon: const Icon(Icons.upload_file),
                label: const Text('Add file'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: _urlController,
                decoration: const InputDecoration(
                  labelText: 'URL',
                  hintText: 'https://example.org/paper.pdf',
                ),
                onSubmitted: (_) => _addUrl(),
              ),
            ),
          ],
        ),
        const Divider(),
        Expanded(
          child: ListView.builder(
            itemCount: sources.length,
            itemBuilder: (_, i) {
              final s = sources[i];
              return ListTile(
                leading: Icon(_iconFor(s.kind)),
                title: Text(s.title),
                subtitle: Text(s.bibliography?.title ?? s.origin),
              );
            },
          ),
        ),
      ],
    );
  }

  IconData _iconFor(SourceKind k) {
    switch (k) {
      case SourceKind.pdf:
        return Icons.picture_as_pdf;
      case SourceKind.docx:
        return Icons.description;
      case SourceKind.markdown:
        return Icons.text_snippet;
      case SourceKind.html:
        return Icons.code;
      case SourceKind.text:
        return Icons.article;
      case SourceKind.url:
        return Icons.link;
    }
  }
}