"""The personas, as the Kotlin tier implements them.

A perspective here is not a job title with the topic pasted into it. The
archetype table supplies the *stance* -- the expert, the skeptic, the
practitioner, the regulator, the analyst, the expositor -- while the
substance of every persona (its description, its focus areas, its guiding
questions and its key inquiries) is taken from the material the persona is
supposed to interrogate.

How a persona is built from that material is a rule, and the rule is Kotlin's:
it is implemented once, in ``lmeditor.discovery``, and the tier serves it
across the NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.perspective``, frozen, so the parity
harness can still ask both sides the same question.

The language model stays Python's. A model is model-bound work: it reads the
context and writes the personas. DSPy is imported only when that path is
taken, so the deterministic door does not need the dependency at all.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.bridge.errors import KotlinDomainError
from lmeditor.core.exceptions import PerspectiveGenerationError, ValidationError
from lmeditor.core.schemas import Perspective
from lmeditor.core.types import PerspectiveRole
from lmeditor.tier import discovery as tier_discovery

if TYPE_CHECKING:
    import dspy

logger = logging.getLogger(__name__)

#: The DSPy module, imported on demand. ``None`` until something asks for it.
_dspy: Any = None


def _dspy_module() -> Any:
    """Import DSPy on demand.

    The deterministic path -- the tier's -- needs no language model, so this
    module must not need DSPy to be imported. Only the language-model path
    does, and it asks for it here.
    """
    global _dspy  # noqa: PLW0603 - the module-level cache of the DSPy module

    if _dspy is None:
        import dspy

        _dspy = dspy
    return _dspy


def _build_signature() -> type:
    """Build the DSPy signature of the persona discovery program."""
    # The module is fetched under a name of its own. A local called ``dspy``
    # would shadow the module-level import of the same name, and the signature
    # -- whose base class is resolved from the module -- would then be built
    # from a name mypy cannot follow.
    dspy_module = _dspy_module()
    # The base class is looked up by name rather than written as a dotted
    # attribute. A dotted base class is a *name* to a type checker, and the
    # module is fetched at run time, so the checker is told the name does not
    # exist. The lookup is the same at run time; only the spelling differs.
    signature_base: Any = getattr(dspy_module, "Signature")

    class PerspectiveDiscoverySignature(signature_base):
        """Discover diverse expert and skeptic personas to research a topic from multiple angles."""

        topic: str = dspy_module.InputField(desc="The core subject, thesis, or problem statement to investigate")
        context: str = dspy_module.InputField(desc="Optional background knowledge, source excerpts, or constraints")
        num_perspectives: int = dspy_module.InputField(desc="Number of distinct personas to generate (between 1 and 10)")
        perspectives_json: str = dspy_module.OutputField(
            desc="JSON array of persona objects with role, title, description, focus_areas, and guiding_questions"
        )

    # The class is built inside a function, so its qualified name would carry
    # the function's name. Put it back to what a module-level class looks like.
    PerspectiveDiscoverySignature.__qualname__ = "PerspectiveDiscoverySignature"
    return PerspectiveDiscoverySignature


def _perspective_signature() -> type:
    """The signature class, built once and then kept."""
    signature = globals().get("PerspectiveDiscoverySignature")
    if signature is None:
        signature = _build_signature()
        globals()["PerspectiveDiscoverySignature"] = signature
    return signature


def __getattr__(name: str) -> Any:
    """Build the DSPy signature on first access (PEP 562).

    ``PerspectiveDiscoverySignature`` is a ``dspy.Signature`` subclass, and a
    signature is needed to *define* it. Building it on first access is what
    lets this module be imported without DSPy installed, while the name stays
    importable exactly as it was.
    """
    if name == "PerspectiveDiscoverySignature":
        return _perspective_signature()
    if name == "dspy":
        return _dspy_module()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class PerspectiveGenerator:
    """Persona synthesizer that grounds every perspective in the supplied context.

    Args:
        use_dspy: Whether to use a language model to write the personas. The
            flag only takes effect when a language model is configured; with
            no model the generator asks the tier for the personas it derives
            from the context.
        model: Optional DSPy language model instance or predictor override.
    """

    #: The archetype table: the stance of each persona role.
    #:
    #: The tier holds the same table (``PerspectiveGenerator.ROLE_ARCHETYPES``)
    #: and is the one that applies it. It is kept here because it is part of
    #: the Python API; the seam does not expose the table.
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
                dspy = _dspy_module()
                self._predictor = dspy.Predict(_perspective_signature())
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
            max_perspectives: Overrides ``num_perspectives`` when supplied.

        Returns:
            List of validated Perspective models.

        Raises:
            ValidationError: If input parameters violate boundary rules.
            PerspectiveGenerationError: If persona synthesis fails.
            BridgeUnavailableError: If the Kotlin tier cannot be reached.
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

        return self._perspectives_from_tier(clean_topic, clean_context, target_count)

    # ------------------------------------------------------------------
    # The seam
    # ------------------------------------------------------------------
    def _perspectives_from_tier(
        self,
        clean_topic: str,
        clean_context: str,
        count: int,
    ) -> list[Perspective]:
        """The tier's answer: the personas it builds from the context."""
        try:
            return tier_discovery.generate_perspectives(clean_topic, clean_context, count)
        except BridgeUnavailableError:
            # The tier is not there. That is an infrastructure failure, not a
            # bad input, and it must not be dressed up as one: the caller needs
            # to know that the rule could not be answered at all.
            raise
        except KotlinDomainError as exc:
            if exc.error_type == "PerspectiveGenerationError":
                raise PerspectiveGenerationError(
                    exc.message,
                    details=dict(exc.details),
                ) from exc
            raise

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

        dspy = _dspy_module()
        predictor = self._predictor or dspy.Predict(_perspective_signature())
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
    "PerspectiveDiscoverySignature",  # noqa: F822 - built on first access (PEP 562)
    "PerspectiveGenerator",
    "StormPerspectiveGenerator",
    "generate_perspectives",
]
