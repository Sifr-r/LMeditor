"""Stanford STORM multi-perspective persona discovery engine for LMeditor."""

from __future__ import annotations

import re
from typing import Any, Sequence
import dspy

from lmeditor.core.exceptions import PerspectiveGenerationError, ValidationError
from lmeditor.core.schemas import Perspective
from lmeditor.core.types import PerspectiveRole


class PerspectiveDiscoverySignature(dspy.Signature):
    """Discover diverse expert and skeptic personas to research a topic from multiple angles."""

    topic: str = dspy.InputField(desc="The core subject, thesis, or problem statement to investigate")
    context: str = dspy.InputField(desc="Optional background knowledge, source excerpts, or constraints")
    num_perspectives: int = dspy.InputField(desc="Number of distinct personas to generate (between 1 and 10)")
    perspectives_json: str = dspy.OutputField(
        desc="JSON array of persona objects with role, title, description, focus_areas, and guiding_questions"
    )


class PerspectiveGenerator:
    """Stanford STORM-style persona synthesizer creating diverse investigative lenses."""

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
            use_dspy: Whether to utilize DSPy LM predictions if configured.
            model: Optional DSPy language model instance or predictor override.
        """
        self.use_dspy = use_dspy
        self.model = model
        self._predictor: dspy.Predict | None = None
        if self.use_dspy:
            try:
                self._predictor = dspy.Predict(PerspectiveDiscoverySignature)
            except Exception:
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
        target_count = max_perspectives if max_perspectives is not None else num_perspectives
        """Generate diverse investigative perspectives for a topic.

        Args:
            topic: Research subject or thesis.
            context: Optional domain context or background excerpts.
            num_perspectives: Count of personas to generate (1 to 10).

        Returns:
            List of validated Perspective models.

        Raises:
            ValidationError: If input parameters violate boundary rules.
            PerspectiveGenerationError: If persona synthesis fails unexpectedly.
        """
        clean_topic = self.validate_inputs(topic, target_count)
        clean_context = (context or "").strip()

        if self.use_dspy and self._predictor is not None:
            try:
                res = self._predictor(
                    topic=clean_topic,
                    context=clean_context or "General domain literature",
                    num_perspectives=target_count,
                )
                parsed = self._try_parse_dspy_output(res.perspectives_json, clean_topic, target_count)
                if parsed:
                    return parsed
            except Exception as exc:
                # Log or fall back deterministically
                pass

        # Deterministic Stanford STORM heuristic generation
        return self._generate_heuristic_perspectives(clean_topic, clean_context, target_count)

    def _generate_heuristic_perspectives(
        self,
        topic: str,
        context: str,
        count: int,
    ) -> list[Perspective]:
        """Produce rich, deterministic perspectives tailored to the topic."""
        perspectives: list[Perspective] = []
        archetypes = self.ROLE_ARCHETYPES

        for idx in range(count):
            role, role_name, base_desc, base_focus, base_questions = archetypes[idx % len(archetypes)]

            # Tailor titles and questions to the specific topic
            persp_id = f"persp_{role.value}_{idx + 1}"
            title = f"{role_name} on {topic}"
            desc = f"{base_desc} Specializing in analyzing '{topic}' with uncompromising domain depth."

            tailored_focus = [f"{focus} in the context of {topic}" for focus in base_focus]
            tailored_questions = [
                q.format(topic=topic) if "{topic}" in q else f"{q} ({topic})"
                for q in base_questions
            ]

            if context:
                tailored_focus.append(f"Empirical grounding against supplied source context ({len(context)} chars)")

            perspective = Perspective(
                perspective_id=persp_id,
                id=persp_id,
                role=role,
                title=title,
                name=title,
                description=desc,
                domain=topic,
                focus_areas=tailored_focus,
                prompt_directives=[
                    f"Adopt the stance of a {role.value}.",
                    f"Interrogate all claims about '{topic}' through rigorous evidence.",
                    "Always mandate source passage attribution.",
                ],
                guiding_questions=tailored_questions,
                key_inquiries=tailored_questions,
            )
            perspectives.append(perspective)

        return perspectives

    def _try_parse_dspy_output(
        self,
        raw_json: str,
        topic: str,
        target_count: int,
    ) -> list[Perspective] | None:
        """Safely parse and validate structured JSON returned from DSPy."""
        import json

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
                focus = item.get("focus_areas", [f"Core mechanics of {topic}"])
                questions = item.get("guiding_questions", [f"What are the main dynamics of {topic}?"])

                perspectives.append(
                    Perspective(
                        perspective_id=pid,
                        id=pid,
                        role=role,
                        title=title,
                        name=title,
                        description=desc,
                        domain=topic,
                        focus_areas=focus,
                        guiding_questions=questions,
                        key_inquiries=questions,
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
