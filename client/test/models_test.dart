import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:client/core/models/chunk.dart';
import 'package:client/core/models/citation.dart';
import 'package:client/core/models/claim.dart';
import 'package:client/core/models/outline.dart';
import 'package:client/core/models/perspective.dart';
import 'package:client/core/theme.dart';

void main() {
  group('SourceChunk Model & Validation', () {
    test('instantiates valid SourceChunk with sound null safety', () {
      final chunk = SourceChunk(
        id: 'chunk_001',
        documentId: 'doc_alpha',
        content: 'Natural Language Inference validates generated claims against retrieved evidence.',
        startChar: 100,
        endChar: 180,
        sha256: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
        metadata: {'source': 'arxiv', 'year': 2024},
      );

      expect(chunk.id, 'chunk_001');
      expect(chunk.chunkId, 'chunk_001');
      expect(chunk.documentId, 'doc_alpha');
      expect(chunk.content, contains('Natural Language Inference'));
      expect(chunk.startChar, 100);
      expect(chunk.endChar, 180);
      expect(chunk.sha256, startsWith('9f86d'));
      expect(chunk.sha256Hash, startsWith('9f86d'));
      expect(chunk.metadata['source'], 'arxiv');
      expect(chunk.metadata['year'], 2024);
      expect(chunk.documentTitle, 'doc_alpha');
    });

    test('boundary validation: throws ArgumentError on invalid inputs', () {
      // Empty id
      expect(
        () => SourceChunk(
          id: '   ',
          documentId: 'doc_1',
          content: 'Some text',
          startChar: 0,
          endChar: 9,
          sha256: 'abc',
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'id')),
      );

      // Empty documentId
      expect(
        () => SourceChunk(
          id: 'chunk_1',
          documentId: '',
          content: 'Some text',
          startChar: 0,
          endChar: 9,
          sha256: 'abc',
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'documentId')),
      );

      // Negative startChar
      expect(
        () => SourceChunk(
          id: 'chunk_1',
          documentId: 'doc_1',
          content: 'Some text',
          startChar: -1,
          endChar: 9,
          sha256: 'abc',
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'startChar')),
      );

      // endChar < startChar
      expect(
        () => SourceChunk(
          id: 'chunk_1',
          documentId: 'doc_1',
          content: 'Some text',
          startChar: 50,
          endChar: 40,
          sha256: 'abc',
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'endChar')),
      );

      // Empty sha256
      expect(
        () => SourceChunk(
          id: 'chunk_1',
          documentId: 'doc_1',
          content: 'Some text',
          startChar: 0,
          endChar: 10,
          sha256: '  ',
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'sha256')),
      );
    });

    test('toJson and fromJson round-trip', () {
      final original = SourceChunk(
        id: 'chunk_42',
        documentId: 'doc_test',
        content: 'Deterministic verifiable writing.',
        startChar: 12,
        endChar: 45,
        sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        metadata: {'author': 'DeepMind', 'page': 4},
      );

      final json = original.toJson();
      expect(json['id'], 'chunk_42');
      expect(json['document_id'], 'doc_test');
      expect(json['start_char'], 12);
      expect(json['end_char'], 45);
      expect(json['sha256'], original.sha256);

      final restored = SourceChunk.fromJson(json);
      expect(restored, equals(original));
      expect(restored.hashCode, equals(original.hashCode));
    });

    test('fromJson handles legacy camelCase and aliases', () {
      final legacyJson = {
        'chunkId': 'legacy_1',
        'documentTitle': 'Legacy Paper',
        'content': 'Legacy content',
        'startChar': 0,
        'endChar': 14,
        'sha256Hash': 'hash123',
        'metadata': {'type': 'legacy'},
      };

      final chunk = SourceChunk.fromJson(legacyJson);
      expect(chunk.id, 'legacy_1');
      expect(chunk.documentId, 'Legacy Paper');
      expect(chunk.sha256, 'hash123');
      expect(chunk.metadata['type'], 'legacy');
    });

    test('copyWith creates updated clone while keeping immutability', () {
      final original = SourceChunk(
        id: 'chunk_1',
        documentId: 'doc_1',
        content: 'Original content',
        startChar: 0,
        endChar: 16,
        sha256: 'hash_original',
      );

      final modified = original.copyWith(
        content: 'Modified content',
        endChar: 16,
      );

      expect(modified.id, original.id);
      expect(modified.documentId, original.documentId);
      expect(modified.content, 'Modified content');
      expect(original.content, 'Original content');
      expect(original == modified, isFalse);
    });

    test('equality and hashCode reflect value semantics including metadata', () {
      final c1 = SourceChunk(
        id: 'c1',
        documentId: 'd1',
        content: 'hello',
        startChar: 0,
        endChar: 5,
        sha256: 'h1',
        metadata: {'k1': 'v1'},
      );

      final c2 = SourceChunk(
        id: 'c1',
        documentId: 'd1',
        content: 'hello',
        startChar: 0,
        endChar: 5,
        sha256: 'h1',
        metadata: {'k1': 'v1'},
      );

      final c3 = SourceChunk(
        id: 'c1',
        documentId: 'd1',
        content: 'hello',
        startChar: 0,
        endChar: 5,
        sha256: 'h1',
        metadata: {'k1': 'v2'},
      );

      expect(c1, equals(c2));
      expect(c1.hashCode, equals(c2.hashCode));
      expect(c1 == c3, isFalse);
    });

    test('toString formats informative description', () {
      final chunk = SourceChunk(
        id: 'c1',
        documentId: 'd1',
        content: 'Verification harness',
        startChar: 0,
        endChar: 20,
        sha256: 'h1',
      );
      expect(chunk.toString(), contains('SourceChunk(id: c1'));
      expect(chunk.toString(), contains('Verification harness'));
    });
  });

  group('CitationLineage Model & Validation', () {
    test('instantiates valid CitationLineage', () {
      final lineage = CitationLineage(
        citationId: 'cit_101',
        sourceChunkId: 'chunk_001',
        prefix: 'As demonstrated by ',
        exactSpan: 'dense passage retrieval models',
        suffix: ', precision scales with depth.',
        startOffset: 120,
        endOffset: 150,
        confidenceScore: 0.98,
      );

      expect(lineage.citationId, 'cit_101');
      expect(lineage.sourceChunkId, 'chunk_001');
      expect(lineage.chunkId, 'chunk_001');
      expect(lineage.prefix, 'As demonstrated by ');
      expect(lineage.exactSpan, 'dense passage retrieval models');
      expect(lineage.targetTokenSpan, 'dense passage retrieval models');
      expect(lineage.suffix, ', precision scales with depth.');
      expect(lineage.startOffset, 120);
      expect(lineage.endOffset, 150);
      expect(lineage.confidenceScore, 0.98);
      expect(lineage.confidence, 0.98);
    });

    test('boundary validation: throws ArgumentError on invalid offsets and scores', () {
      // Empty citationId
      expect(
        () => CitationLineage(
          citationId: '',
          sourceChunkId: 'chunk_1',
          exactSpan: 'span',
          startOffset: 0,
          endOffset: 4,
          confidenceScore: 0.9,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'citationId')),
      );

      // Empty sourceChunkId
      expect(
        () => CitationLineage(
          citationId: 'cit_1',
          sourceChunkId: '  ',
          exactSpan: 'span',
          startOffset: 0,
          endOffset: 4,
          confidenceScore: 0.9,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'sourceChunkId')),
      );

      // Negative startOffset
      expect(
        () => CitationLineage(
          citationId: 'cit_1',
          sourceChunkId: 'chunk_1',
          exactSpan: 'span',
          startOffset: -5,
          endOffset: 4,
          confidenceScore: 0.9,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'startOffset')),
      );

      // startOffset > endOffset
      expect(
        () => CitationLineage(
          citationId: 'cit_1',
          sourceChunkId: 'chunk_1',
          exactSpan: 'span',
          startOffset: 20,
          endOffset: 10,
          confidenceScore: 0.9,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'endOffset')),
      );

      // confidenceScore < 0.0
      expect(
        () => CitationLineage(
          citationId: 'cit_1',
          sourceChunkId: 'chunk_1',
          exactSpan: 'span',
          startOffset: 0,
          endOffset: 4,
          confidenceScore: -0.1,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'confidenceScore')),
      );

      // confidenceScore > 1.0
      expect(
        () => CitationLineage(
          citationId: 'cit_1',
          sourceChunkId: 'chunk_1',
          exactSpan: 'span',
          startOffset: 0,
          endOffset: 4,
          confidenceScore: 1.05,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'confidenceScore')),
      );
    });

    test('toJson and fromJson round-trip', () {
      final lineage = CitationLineage(
        citationId: 'cit_55',
        sourceChunkId: 'chunk_99',
        prefix: 'pre ',
        exactSpan: 'evidence span',
        suffix: ' post',
        startOffset: 10,
        endOffset: 23,
        confidenceScore: 0.92,
      );

      final json = lineage.toJson();
      expect(json['citation_id'], 'cit_55');
      expect(json['source_chunk_id'], 'chunk_99');
      expect(json['confidence_score'], 0.92);

      final restored = CitationLineage.fromJson(json);
      expect(restored, equals(lineage));
      expect(restored.hashCode, equals(lineage.hashCode));
    });

    test('copyWith and value equality', () {
      final original = CitationLineage(
        citationId: 'cit_1',
        sourceChunkId: 'chunk_1',
        exactSpan: 'text',
        startOffset: 0,
        endOffset: 4,
        confidenceScore: 0.8,
      );

      final clone = original.copyWith(confidenceScore: 0.95);
      expect(clone.confidenceScore, 0.95);
      expect(clone.citationId, original.citationId);
      expect(original == clone, isFalse);
    });
  });

  group('Claim Model, NLILabel & NLI Status Getters', () {
    test('instantiates valid Claim', () {
      final claim = Claim(
        claimId: 'claim_1',
        sentence: 'Transformer models leverage self-attention mechanisms.',
        proposition: 'Transformers utilize self-attention.',
        citationIds: ['cit_1', 'cit_2'],
        entailmentScore: 0.88,
        label: NLILabel.entailment,
        premiseChunkId: 'chunk_007',
        explanation: 'Direct entailment supported by Vaswani et al.',
      );

      expect(claim.claimId, 'claim_1');
      expect(claim.sentence, contains('Transformer models'));
      expect(claim.fullSentence, contains('Transformer models'));
      expect(claim.proposition, 'Transformers utilize self-attention.');
      expect(claim.citationIds, ['cit_1', 'cit_2']);
      expect(claim.citationId, 'cit_1');
      expect(claim.entailmentScore, 0.88);
      expect(claim.confidence, 0.88);
      expect(claim.label, NLILabel.entailment);
      expect(claim.nliLabel, NLILabel.entailment);
      expect(claim.premiseChunkId, 'chunk_007');
      expect(claim.chunkId, 'chunk_007');
      expect(claim.explanation, contains('Direct entailment'));
    });

    test('boundary validation: throws ArgumentError on invalid inputs', () {
      expect(
        () => Claim(
          claimId: '',
          sentence: 'valid sentence',
          proposition: 'valid prop',
          entailmentScore: 0.5,
          label: NLILabel.neutral,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'claimId')),
      );

      expect(
        () => Claim(
          claimId: 'c1',
          sentence: '   ',
          proposition: 'valid prop',
          entailmentScore: 0.5,
          label: NLILabel.neutral,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'sentence')),
      );

      expect(
        () => Claim(
          claimId: 'c1',
          sentence: 'sentence',
          proposition: '',
          entailmentScore: 0.5,
          label: NLILabel.neutral,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'proposition')),
      );

      expect(
        () => Claim(
          claimId: 'c1',
          sentence: 'sentence',
          proposition: 'prop',
          entailmentScore: -0.01,
          label: NLILabel.neutral,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'entailmentScore')),
      );

      expect(
        () => Claim(
          claimId: 'c1',
          sentence: 'sentence',
          proposition: 'prop',
          entailmentScore: 1.01,
          label: NLILabel.neutral,
        ),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'entailmentScore')),
      );
    });

    test('status getters: isVerified, isContradiction, isUngrounded', () {
      // Verified: entailment >= 0.7
      final verifiedClaim = Claim(
        claimId: 'v1',
        sentence: 'Sent 1',
        proposition: 'Prop 1',
        entailmentScore: 0.70,
        label: NLILabel.entailment,
      );
      expect(verifiedClaim.isVerified, isTrue);
      expect(verifiedClaim.isContradiction, isFalse);
      expect(verifiedClaim.isUngrounded, isFalse);

      // Entailment below 0.7 is NOT verified
      final weakEntailment = Claim(
        claimId: 'v2',
        sentence: 'Sent 2',
        proposition: 'Prop 2',
        entailmentScore: 0.69,
        label: NLILabel.entailment,
      );
      expect(weakEntailment.isVerified, isFalse);
      expect(weakEntailment.isContradiction, isFalse);
      expect(weakEntailment.isUngrounded, isFalse);

      // Contradiction: isContradiction and isUngrounded are TRUE
      final contradictionClaim = Claim(
        claimId: 'c1',
        sentence: 'Sent 3',
        proposition: 'Prop 3',
        entailmentScore: 0.95,
        label: NLILabel.contradiction,
      );
      expect(contradictionClaim.isVerified, isFalse);
      expect(contradictionClaim.isContradiction, isTrue);
      expect(contradictionClaim.isUngrounded, isTrue);

      // Ungrounded: isUngrounded is TRUE, isContradiction is FALSE
      final ungroundedClaim = Claim(
        claimId: 'u1',
        sentence: 'Sent 4',
        proposition: 'Prop 4',
        entailmentScore: 0.10,
        label: NLILabel.ungrounded,
      );
      expect(ungroundedClaim.isVerified, isFalse);
      expect(ungroundedClaim.isContradiction, isFalse);
      expect(ungroundedClaim.isUngrounded, isTrue);

      // Neutral
      final neutralClaim = Claim(
        claimId: 'n1',
        sentence: 'Sent 5',
        proposition: 'Prop 5',
        entailmentScore: 0.50,
        label: NLILabel.neutral,
      );
      expect(neutralClaim.isVerified, isFalse);
      expect(neutralClaim.isContradiction, isFalse);
      expect(neutralClaim.isUngrounded, isFalse);
    });

    test('NLILabel enum parsing, presentation, and attributes', () {
      expect(NLILabel.fromString('entailment'), NLILabel.entailment);
      expect(NLILabel.fromString('verified'), NLILabel.entailment);
      expect(NLILabel.fromString('contradiction'), NLILabel.contradiction);
      expect(NLILabel.fromString('neutral'), NLILabel.neutral);
      expect(NLILabel.fromString('ungrounded'), NLILabel.ungrounded);
      expect(NLILabel.fromString(null), NLILabel.neutral);

      expect(() => NLILabel.fromString('invalid_label'), throwsArgumentError);

      expect(NLILabel.entailment.displayName, 'Verified');
      expect(NLILabel.contradiction.displayName, 'Contradiction');
      expect(NLILabel.neutral.displayName, 'Neutral');
      expect(NLILabel.ungrounded.displayName, 'Ungrounded');

      expect(NLILabel.entailment.color, AppColors.verifiedGreen);
      expect(NLILabel.contradiction.color, AppColors.contradictionRed);
      expect(NLILabel.neutral.color, AppColors.neutralYellow);
      expect(NLILabel.ungrounded.color, AppColors.contradictionRed);
    });

    test('toJson and fromJson round-trip with list and single citation_id', () {
      final claim = Claim(
        claimId: 'claim_123',
        sentence: 'Sentence text',
        proposition: 'Proposition text',
        citationIds: ['cit_a', 'cit_b'],
        entailmentScore: 0.85,
        label: NLILabel.entailment,
        premiseChunkId: 'chunk_p',
        explanation: 'NLI model output explanation',
      );

      final json = claim.toJson();
      expect(json['claim_id'], 'claim_123');
      expect(json['entailment_score'], 0.85);
      expect(json['label'], 'entailment');

      final restored = Claim.fromJson(json);
      expect(restored, equals(claim));
      expect(restored.hashCode, equals(claim.hashCode));

      // Single citation_id string in json
      final singleCitJson = {
        'claimId': 'c_single',
        'sentence': 'Sentence',
        'proposition': 'Prop',
        'citation_id': 'cit_single',
        'confidence': 0.75,
        'label': 'verified',
      };
      final parsedSingle = Claim.fromJson(singleCitJson);
      expect(parsedSingle.citationIds, ['cit_single']);
      expect(parsedSingle.isVerified, isTrue);
    });

    test('copyWith updates fields deterministically', () {
      final claim = Claim(
        claimId: 'c1',
        sentence: 'sentence',
        proposition: 'prop',
        entailmentScore: 0.5,
        label: NLILabel.neutral,
      );

      final updated = claim.copyWith(
        entailmentScore: 0.95,
        label: NLILabel.entailment,
      );

      expect(updated.isVerified, isTrue);
      expect(claim.isVerified, isFalse);
    });
  });

  group('OutlineNode Model & Recursive Hierarchy', () {
    test('instantiates valid leaf OutlineNode', () {
      final leaf = OutlineNode(
        id: 'node_1',
        title: 'Introduction & Problem Statement',
        level: 1,
        sectionGoal: 'Define the motivation and mathematical scope of long-form generation.',
        assignedChunkIds: ['chunk_01', 'chunk_02'],
        isCompleted: true,
      );

      expect(leaf.id, 'node_1');
      expect(leaf.title, 'Introduction & Problem Statement');
      expect(leaf.level, 1);
      expect(leaf.sectionGoal, contains('Define the motivation'));
      expect(leaf.goal, leaf.sectionGoal);
      expect(leaf.assignedChunkIds, ['chunk_01', 'chunk_02']);
      expect(leaf.subsections, isEmpty);
      expect(leaf.children, isEmpty);
      expect(leaf.isCompleted, isTrue);
    });

    test('boundary validation: throws ArgumentError on invalid inputs', () {
      expect(
        () => OutlineNode(id: '', title: 'Title', level: 0),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'id')),
      );

      expect(
        () => OutlineNode(id: 'n1', title: '   ', level: 0),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'title')),
      );

      expect(
        () => OutlineNode(id: 'n1', title: 'Title', level: -1),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'level')),
      );
    });

    test('recursive toJson and fromJson preserves multi-level outline trees', () {
      final root = OutlineNode(
        id: 'root',
        title: 'Monograph: Verified Language Synthesis',
        level: 0,
        sectionGoal: 'Complete manuscript hierarchy',
        subsections: [
          OutlineNode(
            id: 'sec_1',
            title: 'Theoretical Foundations',
            level: 1,
            sectionGoal: 'Establish grounding bounds',
            assignedChunkIds: ['chunk_10', 'chunk_11'],
            subsections: [
              OutlineNode(
                id: 'sec_1_1',
                title: 'Natural Language Inference Metrics',
                level: 2,
                sectionGoal: 'Deconstruct DeBERTa entailment matrix',
                assignedChunkIds: ['chunk_12'],
                isCompleted: true,
              ),
            ],
          ),
          OutlineNode(
            id: 'sec_2',
            title: 'Empirical Results',
            level: 1,
            sectionGoal: 'Benchmark citation precision',
            assignedChunkIds: ['chunk_20'],
          ),
        ],
      );

      final json = root.toJson();
      expect(json['id'], 'root');
      expect((json['subsections'] as List).length, 2);

      final restored = OutlineNode.fromJson(json);
      expect(restored, equals(root));
      expect(restored.subsections.length, 2);
      expect(restored.subsections[0].subsections.length, 1);
      expect(restored.subsections[0].subsections[0].title, 'Natural Language Inference Metrics');
      expect(restored.subsections[0].subsections[0].isCompleted, isTrue);
      expect(restored.hashCode, equals(root.hashCode));
    });

    test('copyWith updates nested structure and maintains equality semantics', () {
      final node = OutlineNode(
        id: 'node_1',
        title: 'Title 1',
        level: 1,
        assignedChunkIds: ['c1'],
      );

      final modified = node.copyWith(
        title: 'Title 2',
        isCompleted: true,
      );

      expect(modified.title, 'Title 2');
      expect(modified.isCompleted, isTrue);
      expect(modified.assignedChunkIds, ['c1']);
      expect(node == modified, isFalse);
    });
  });

  group('Perspective Model (STORM Personas)', () {
    test('instantiates valid Perspective with focus queries', () {
      final persona = Perspective(
        id: 'persp_historian',
        name: 'Epistemological Historian',
        role: 'Scientific Lineage Auditor',
        description: 'Examines the foundational origins and chronological evolution of hypotheses.',
        focusQueries: [
          'What historical experiments first disproved ungrounded text generation?',
          'How did NLI benchmarks transition from SNLI to MultiNLI and modern factuality judges?',
        ],
      );

      expect(persona.id, 'persp_historian');
      expect(persona.name, 'Epistemological Historian');
      expect(persona.role, 'Scientific Lineage Auditor');
      expect(persona.description, contains('foundational origins'));
      expect(persona.focusQueries.length, 2);
      expect(persona.focusQuestions, persona.focusQueries);
      expect(persona.isSelected, isTrue);
      expect(persona.avatarSymbol, '🔬');
    });

    test('boundary validation: throws ArgumentError on missing attributes', () {
      expect(
        () => Perspective(id: '', name: 'Name', role: 'Role'),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'id')),
      );

      expect(
        () => Perspective(id: 'p1', name: '  ', role: 'Role'),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'name')),
      );

      expect(
        () => Perspective(id: 'p1', name: 'Name', role: ''),
        throwsA(isA<ArgumentError>().having((e) => e.name, 'name', 'role')),
      );
    });

    test('toJson and fromJson round-trip', () {
      final persona = Perspective(
        id: 'persp_critic',
        name: 'Critical Empirical Skeptic',
        role: 'Hallucination Auditor',
        description: 'Seeks counter-evidence and ungrounded statements.',
        focusQueries: ['Where does this draft fail to cite its strongest claim?'],
        isSelected: false,
        avatarSymbol: '⚖️',
      );

      final json = persona.toJson();
      expect(json['id'], 'persp_critic');
      expect(json['is_selected'], isFalse);
      expect(json['avatar_symbol'], '⚖️');

      final restored = Perspective.fromJson(json);
      expect(restored, equals(persona));
      expect(restored.hashCode, equals(persona.hashCode));
    });

    test('StormPerspective typedef compatibility', () {
      final StormPerspective alias = Perspective(
        id: 'alias_1',
        name: 'Test',
        role: 'Test Role',
        focusQueries: ['Q1'],
      );

      expect(alias, isA<Perspective>());
      expect(alias.focusQuestions, ['Q1']);
    });
  });

  group('Typography, Expository Theme & Color Standards', () {
    test('verifies exact NLI status color constants', () {
      expect(AppColors.verifiedGreen, const Color(0xFF10B981));
      expect(AppColors.contradictionRed, const Color(0xFFEF4444));
      expect(AppColors.neutralYellow, const Color(0xFFF59E0B));
      expect(AppColors.ungroundedGray, const Color(0xFF6B7280));

      expect(StudioTheme.verifiedEmerald, const Color(0xFF10B981));
      expect(StudioTheme.contradictionCrimson, const Color(0xFFEF4444));
      expect(StudioTheme.neutralAmber, const Color(0xFFF59E0B));
      expect(StudioTheme.ungroundedGray, const Color(0xFF6B7280));
    });

    test('verifies surface and structural palette colors', () {
      expect(AppColors.background, const Color(0xFF0B0F17));
      expect(AppColors.surface, const Color(0xFF111827));
      expect(AppColors.card, const Color(0xFF1A2234));
      expect(AppColors.border, const Color(0xFF2A364F));
      expect(AppColors.textPrimary, const Color(0xFFF8FAFC));
      expect(AppColors.textSecondary, const Color(0xFF94A3B8));
    });

    test('expository TextTheme adheres to 1.6 line height and serif hierarchy', () {
      final textTheme = LMeditorTheme.createExpositoryTextTheme();

      expect(textTheme.headlineLarge?.height, 1.6);
      expect(textTheme.headlineMedium?.height, 1.6);
      expect(textTheme.headlineSmall?.height, 1.6);
      expect(textTheme.titleLarge?.height, 1.6);
      expect(textTheme.titleMedium?.height, 1.6);
      expect(textTheme.bodyLarge?.height, 1.6);
      expect(textTheme.bodyMedium?.height, 1.6);
      expect(textTheme.bodySmall?.height, 1.6);

      // Verify serif family and fallbacks
      expect(textTheme.bodyLarge?.fontFamily, 'Georgia');
      expect(textTheme.bodyLarge?.fontFamilyFallback, contains('Merriweather'));
      expect(textTheme.headlineLarge?.fontFamily, 'Merriweather');
      expect(textTheme.headlineLarge?.fontFamilyFallback, contains('Georgia'));
    });

    test('darkTheme configures desktop scrollbar and card chrome', () {
      final theme = LMeditorTheme.darkTheme;

      expect(theme.brightness, Brightness.dark);
      expect(theme.scaffoldBackgroundColor, AppColors.background);
      expect(theme.colorScheme.surface, AppColors.surface);
      expect(theme.colorScheme.error, AppColors.contradictionRed);

      // Scrollbar checks
      final scrollbarTheme = theme.scrollbarTheme;
      expect(scrollbarTheme.thickness?.resolve({}), 8.0);
      expect(scrollbarTheme.radius, const Radius.circular(4.0));
      expect(scrollbarTheme.thumbVisibility?.resolve({}), isTrue);

      // Card theme checks
      final cardTheme = theme.cardTheme;
      expect(cardTheme.color, AppColors.card);
      expect(cardTheme.elevation, 0);
      expect(cardTheme.shape, isA<RoundedRectangleBorder>());
    });
  });
}
