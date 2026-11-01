"""The frozen multi-perspective persona discovery.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.

It is frozen. The archetype table and the way a persona is built from the
material it reads are the tier's rules now: the live module
``lmeditor.discovery.perspective`` is the door to them, and this copy is the
yardstick they are measured against.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

import dspy

from lmeditor.core.exceptions import PerspectiveGenerationError, ValidationError
from lmeditor.core.schemas import Perspective
from lmeditor.core.types import PerspectiveRole
from lmeditor._reference.discovery_source_analysis import (
    GENERIC_TERMS,
    extract_findings,
    salient_terms,
)

logger = logging.getLogger(__name__)


def _as_clause(finding: str) -> str:
    """``finding`` as a clause that can sit inside another sentence.

    A finding is a whole sentence, so it carries its own terminal punctuation.
    Interpolating one verbatim into the middle of a question would otherwise
    produce a double mark ("...within days.?").
    """
    return finding.rstrip(" .;,:")


class PerspectiveDiscoverySignature(dspy.Signature):  # type: ignore[misc]
    """Discover diverse expert and skeptic personas to research a topic from multiple angles."""

    topic: str = dspy.InputField(desc="The core subject, thesis, or problem statement to investigate")
    context: str = dspy.InputField(desc="Optional background knowledge, source excerpts, or constraints")
    num_perspectives: int = dspy.InputField(desc="Number of distinct personas to generate (between 1 and 10)")
    perspectives_json: str = dspy.OutputField(
        desc="JSON array of persona objects with role, title, description, focus_areas, and guiding_questions"
    )


class PerspectiveGenerator:
    """Persona synthesizer that grounds every perspective in the supplied context.

    Args:
        use_dspy: Whether to use a language model to write the personas. The
            flag only takes effect when a language model is configured; with
            no model the generator derives the personas deterministically
            from the context.
        model: Optional DSPy language model instance or predictor override.
    """

    ROLE_ARCHETYPES: list[tuple[PerspectiveRole, str, str, list[str], list[str]]] = [
        (
            PerspectiveRole.DOMAIN_EXPERT,
            "Lead Systems & Domain Specialist",
            "Deep subject-matter expert focused on formal theoretical rigor, architectural patterns, and mechanistic correctness.",
            [
                "Formal theoretical foundations and core mathematical abstractions",
                "Mechanistic execution models and data-flow invariants",
                "Architectural trade-offs and computational scalability",
            ],
            [
                "What are the foundational theoretical assumptions underlying {topic}?",
                "How does the internal execution model guarantee correctness and bounded complexity?",
                "What formal proofs or empirical benchmarks establish the system's claims?",
            ],
        ),
        (
            PerspectiveRole.SKEPTIC,
            "Empirical Critic & Adversarial Skeptic",
            "Critical interrogator seeking edge cases, ungrounded assumptions, empirical failure modes, and trade-off costs.",
            [
                "Edge-case vulnerabilities and failure boundaries",
                "Empirical ungroundedness or reproducibility gaps",
                "Hidden operational costs and latency penalties",
            ],
            [
                "Under what specific stress conditions or adversarial inputs does {topic} fail?",
                "Are the reported efficacy metrics vulnerable to data contamination or cherry-picking?",
                "What non-trivial overheads or compromises are minimized in standard discussions?",
            ],
        ),
        (
            PerspectiveRole.PRACTITIONER,
            "Production Systems & Deployment Engineer",
            "Pragmatic builder addressing real-world integration ergonomics, telemetry, lifecycle reliability, and operational toil.",
            [
                "Production deployment friction and operational ergonomics",
                "Runtime observability, latency, and fault recovery",
                "Developer velocity, maintenance overhead, and system migration",
            ],
            [
                "How does {topic} perform under live, continuous integration and production loads?",
                "What diagnostic tooling and rollback strategies exist when anomalies emerge?",
                "How does this approach compare in maintenance overhead against legacy stacks?",
            ],
        ),
        (
            PerspectiveRole.REGULATOR,
            "Governance, Safety & Compliance Auditor",
            "Risk analyst focused on verification provenance, algorithmic safety, ethical boundaries, and compliance adherence.",
            [
                "Provable attribution, cryptographic provenance, and auditability",
                "Compliance standards, safety boundaries, and ethical implications",
                "Data sovereignty, privacy guarantees, and alignment assurance",
            ],
            [
                "How are verifiable citations and mathematical provenance enforced throughout {topic}?",
                "What automated safeguards prevent hallucination or unauthorized data leakage?",
                "Does the system satisfy strict reproducibility and regulatory transparency mandates?",
            ],
        ),
        (
            PerspectiveRole.COMPARATIVE_ANALYST,
            "Comparative Systems & Paradigm Strategist",
            "Cross-disciplinary analyst contextualizing current methodologies against competing historical and contemporary approaches.",
            [
                "Direct comparative analysis against competitive alternatives",
                "Historical paradigm shifts and evolutionary trajectory",
                "Quantitative Pareto frontiers of quality, cost, and throughput",
            ],
            [
                "Where does {topic} sit on the Pareto frontier relative to existing alternatives?",
                "What prior paradigms attempted this design, and what novel factors enable it now?",
                "Which specific problem domains favor alternative architectures instead?",
            ],
        ),
        (
            PerspectiveRole.GENERAL_AUDIENCE,
            "Expository Communicator & Conceptual Synthesizer",
            "Intuitive expositor focused on accessible conceptual framing, high-level motivations, and mental models.",
            [
                "Intuitive mental models and conceptual taxonomies",
                "Real-world relevance and high-level motivation",
                "Structural narrative clarity and progressive disclosure",
            ],
            [
                "What is the simplest mental model that captures the core essence of {topic}?",
                "Why is this subject consequential to non-specialist stakeholders?",
                "What analogies best communicate the paradigm shift without sacrificing fidelity?",
            ],
        ),
    ]

    def __init__(self, use_dspy: bool = False, model: Any = None) -> None:
        """Initialize the perspective generator.

        Args:
            use_dspy: Whether to use a language model to write the personas.
            model: Optional DSPy language model instance or predictor override.
        """
        self.use_dspy = use_dspy
        self.model = model
        self._predictor: dspy.Predict | None = None
        if self.use_dspy:
            try:
                self._predictor = dspy.Predict(PerspectiveDiscoverySignature)
            except Exception as exc:  # noqa: BLE001 - a broken DSPy install is reported below
                logger.warning("Could not build the perspective predictor: %s", exc)
                self._predictor = None

    def validate_inputs(self, topic: str, num_perspectives: int) -> str:
        """Validate topic and count boundaries strictly.

        Args:
            topic: Manuscript subject or thesis.
            num_perspectives: Target number of personas.

        Returns:
            Sanitized topic string.

        Raises:
            ValidationError: If boundaries are breached.
        """
        if not isinstance(topic, str):
            raise ValidationError(
                f"Topic must be a string, got {type(topic).__name__}",
                field="topic",
                value=topic,
            )
        cleaned_topic = topic.strip()
        if not cleaned_topic:
            raise ValidationError("Topic cannot be empty or whitespace.", field="topic", value=topic)
        if len(cleaned_topic) < 3 or len(cleaned_topic) > 1000:
            raise ValidationError(
                f"Topic length must be between 3 and 1000 characters, got {len(cleaned_topic)}",
                field="topic",
                value=cleaned_topic,
            )

        if not isinstance(num_perspectives, int) or isinstance(num_perspectives, bool):
            raise ValidationError(
                f"num_perspectives must be an integer, got {type(num_perspectives).__name__}",
                field="num_perspectives",
                value=num_perspectives,
            )
        if num_perspectives < 1 or num_perspectives > 10:
            raise ValidationError(
                f"num_perspectives must be between 1 and 10, got {num_perspectives}",
                field="num_perspectives",
                value=num_perspectives,
            )

        return cleaned_topic

    def generate_perspectives(
        self,
        topic: str,
        context: str | None = None,
        num_perspectives: int = 4,
        max_perspectives: int | None = None,
    ) -> list[Perspective]:
        """Generate diverse investigative perspectives for a topic.

        Args:
            topic: Research subject or thesis.
            context: Optional domain context or background excerpts. The
                personas are grounded in this material.
            num_perspectives: Count of personas to generate (1 to 10).

        Returns:
            List of validated Perspective models.

        Raises:
            ValidationError: If input parameters violate boundary rules.
            PerspectiveGenerationError: If persona synthesis fails.
        """
        target_count = max_perspectives if max_perspectives is not None else num_perspectives
        clean_topic = self.validate_inputs(topic, target_count)
        clean_context = (context or "").strip()

        if self.use_dspy:
            from lmeditor.llm import is_configured

            if is_configured():
                return self._generate_perspectives_with_lm(clean_topic, clean_context, target_count)
            logger.info(
                "Perspective generation is running without a language model; "
                "deriving the personas from the supplied context instead."
            )

        return self._generate_heuristic_perspectives(clean_topic, clean_context, target_count)

    # ------------------------------------------------------------------
    # Language-model path
    # ------------------------------------------------------------------
    def _generate_perspectives_with_lm(
        self,
        topic: str,
        context: str,
        count: int,
    ) -> list[Perspective]:
        """Generate the personas with the configured language model.

        A model that fails, or that answers with something unusable, is a
        real failure: it raises rather than silently degrading to a generic
        persona list.
        """
        from lmeditor.llm import require_lm

        require_lm("perspective discovery")

        predictor = self._predictor or dspy.Predict(PerspectiveDiscoverySignature)
        try:
            res = predictor(
                topic=topic,
                context=context or "General domain literature",
                num_perspectives=count,
            )
        except Exception as exc:
            raise PerspectiveGenerationError(
                f"Language model failed to generate perspectives: {exc}",
                details={"topic": topic, "num_perspectives": count},
            ) from exc

        raw = str(getattr(res, "perspectives_json", "") or "")
        parsed = self._try_parse_dspy_output(raw, topic, count)
        if not parsed:
            raise PerspectiveGenerationError(
                "Language model returned no usable perspective payload.",
                details={"topic": topic, "raw_output": raw[:500]},
            )
        return parsed

    # ------------------------------------------------------------------
    # Deterministic path
    # ------------------------------------------------------------------
    def _generate_heuristic_perspectives(
        self,
        topic: str,
        context: str,
        count: int,
    ) -> list[Perspective]:
        """Produce deterministic perspectives grounded in the supplied context."""
        perspectives: list[Perspective] = []
        archetypes = self.ROLE_ARCHETYPES

        terms = [term for term in salient_terms(context, limit=10) if term not in GENERIC_TERMS]
        findings = extract_findings(context, limit=8)

        for idx in range(count):
            role, role_name, base_desc, base_focus, base_questions = archetypes[idx % len(archetypes)]

            persp_id = f"persp_{role.value}_{idx + 1}"
            title = f"{role_name} on {topic}"

            # Every persona anchors on the terms of the material it will read
            # and on the findings that material actually states.
            persona_terms = self._rotate(terms, idx, limit=4)
            persona_findings = self._rotate(findings, idx, limit=3)

            description = self._build_description(
                base_desc=base_desc,
                topic=topic,
                terms=persona_terms,
                findings=persona_findings,
                context=context,
            )
            focus_areas = self._build_focus_areas(base_focus, topic, persona_terms, persona_findings)
            guiding_questions = self._build_guiding_questions(
                base_questions, topic, persona_terms, persona_findings
            )
            key_inquiries = self._build_key_inquiries(role, topic, persona_terms, persona_findings)

            perspective = Perspective(
                perspective_id=persp_id,
                id=persp_id,
                role=role,
                title=title,
                name=title,
                description=description,
                domain=topic,
                focus_areas=focus_areas,
                prompt_directives=[
                    f"Adopt the stance of a {role.value}.",
                    f"Interrogate all claims about '{topic}' through rigorous evidence.",
                    "Always mandate source passage attribution.",
                    *(
                        [f"Anchor every answer in the supplied material on {persona_terms[0]}."]
                        if persona_terms
                        else []
                    ),
                ],
                guiding_questions=guiding_questions,
                key_inquiries=key_inquiries,
            )
            perspectives.append(perspective)

        return perspectives

    @staticmethod
    def _rotate(items: Sequence[str], offset: int, limit: int) -> list[str]:
        """Take ``limit`` items from ``items``, starting at ``offset``.

        Rotating gives each archetype a different slice of the material, so
        two personas do not interrogate the same passage.
        """
        if not items:
            return []
        count = len(items)
        return [items[(offset + step) % count] for step in range(min(limit, count))]

    @staticmethod
    def _build_description(
        base_desc: str,
        topic: str,
        terms: Sequence[str],
        findings: Sequence[str],
        context: str,
    ) -> str:
        """Describe the persona in terms of the material it reads."""
        if not context.strip():
            return f"{base_desc} Specializing in analyzing '{topic}' with uncompromising domain depth."

        parts = [f"{base_desc} Specializing in analyzing '{topic}' with uncompromising domain depth."]
        if terms:
            parts.append(
                f"This persona reads the supplied material through {', '.join(terms[:3])}, "
                "and treats what that material reports as the primary evidence."
            )
        if findings:
            parts.append(f"Its point of departure is the reported finding: \"{findings[0]}\"")
        return " ".join(parts)

    @staticmethod
    def _build_focus_areas(
        base_focus: Sequence[str],
        topic: str,
        terms: Sequence[str],
        findings: Sequence[str],
    ) -> list[str]:
        """Ground each archetype focus area in a term of the source material."""
        focus_areas: list[str] = []
        for idx, focus in enumerate(base_focus):
            if terms:
                anchor = terms[idx % len(terms)]
                focus_areas.append(f"{focus}, read against the evidence on {anchor}")
            else:
                focus_areas.append(f"{focus} in the context of {topic}")

        for finding in findings[:2]:
            focus_areas.append(f"Interrogating the claim that {finding}")

        return focus_areas

    @staticmethod
    def _build_guiding_questions(
        base_questions: Sequence[str],
        topic: str,
        terms: Sequence[str],
        findings: Sequence[str],
    ) -> list[str]:
        """Ground each archetype question in the source material."""
        questions: list[str] = []
        for idx, question in enumerate(base_questions):
            framed = question.format(topic=topic) if "{topic}" in question else question
            if terms:
                anchor = terms[idx % len(terms)]
                questions.append(f"{framed} Ground the answer in what the source says about {anchor}.")
            else:
                questions.append(f"{framed} ({topic})")

        for finding in findings[:2]:
            questions.append(
                f"The source states: \"{finding}\" — does this support or undercut the claim, and by what mechanism?"
            )

        return questions

    @staticmethod
    def _build_key_inquiries(
        role: PerspectiveRole,
        topic: str,
        terms: Sequence[str],
        findings: Sequence[str],
    ) -> list[str]:
        """The role-specific inquiries, each anchored in the source material."""
        inquiries: list[str] = []
        if terms:
            inquiries.append(
                f"Which passage about {terms[0]} most constrains the {role.value} reading of {topic}?"
            )
        if len(terms) > 1:
            inquiries.append(
                f"How do {terms[1]} and {terms[0]} interact in the material, and what follows for {topic}?"
            )
        if findings:
            inquiries.append(
                f"What evidence would falsify the reported claim that {_as_clause(findings[0])}?"
            )
        inquiries.append(f"What does the material not settle about {topic}, and what would settle it?")
        return inquiries

    def _try_parse_dspy_output(
        self,
        raw_json: str,
        topic: str,
        target_count: int,
    ) -> list[Perspective] | None:
        """Safely parse and validate structured JSON returned from DSPy."""
        try:
            # Extract JSON array from possible markdown fence
            cleaned = raw_json.strip()
            if "```" in cleaned:
                match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", cleaned, re.DOTALL)
                if match:
                    cleaned = match.group(1)
            data = json.loads(cleaned)
            if not isinstance(data, list):
                return None

            perspectives: list[Perspective] = []
            for idx, item in enumerate(data[:target_count]):
                if not isinstance(item, dict):
                    continue
                role_str = item.get("role", PerspectiveRole.DOMAIN_EXPERT.value)
                try:
                    role = PerspectiveRole(role_str)
                except ValueError:
                    role = PerspectiveRole.DOMAIN_EXPERT

                pid = item.get("id") or f"persp_{role.value}_{idx + 1}"
                title = item.get("title") or item.get("name") or f"{role.value.capitalize()} Specialist"
                desc = item.get("description", f"Expert investigating {topic}")
                focus = item.get("focus_areas") or [f"Core mechanics of {topic}"]
                questions = item.get("guiding_questions") or [f"What are the main dynamics of {topic}?"]
                inquiries = item.get("key_inquiries") or questions

                perspectives.append(
                    Perspective(
                        perspective_id=pid,
                        id=pid,
                        role=role,
                        title=title,
                        name=title,
                        description=desc,
                        domain=topic,
                        focus_areas=list(focus),
                        guiding_questions=list(questions),
                        key_inquiries=list(inquiries),
                    )
                )

            if len(perspectives) >= target_count:
                return perspectives[:target_count]
            return None
        except Exception:
            return None


def generate_perspectives(
    topic: str,
    context: str | None = None,
    num_perspectives: int = 4,
) -> list[Perspective]:
    """Convenience functional interface to PerspectiveGenerator."""
    generator = PerspectiveGenerator()
    return generator.generate_perspectives(topic=topic, context=context, num_perspectives=num_perspectives)


StormPerspectiveGenerator = PerspectiveGenerator

__all__ = [
    "PerspectiveDiscoverySignature",
    "PerspectiveGenerator",
    "StormPerspectiveGenerator",
    "generate_perspectives",
]
