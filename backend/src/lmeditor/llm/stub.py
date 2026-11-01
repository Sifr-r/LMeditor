"""A deterministic, grounded language model for offline use.

LMeditor is a *writing* harness: without a language model it cannot write, and
it refuses to fabricate prose in place of a model call. That makes the
application impossible to try on a machine with no model provider
configured -- which is a poor showing for a local-first tool.

This module provides a stand-in that is honest about what it is. It is not a
language model; it is a **deterministic drafter** that lifts the passage it
was given and cites that passage's real chunk id. Because it quotes its
source, the verification stage still has something real to check, and the
grounding guarantees the product makes remain meaningful.

Select it explicitly::

    LMEDITOR_LM_PROVIDER=stub

The provider reports itself as ``stub`` everywhere the model is named -- in
``/api/v1/health``, in the scorecard's ``evaluator`` field and in the run
log -- so a document produced this way can never be mistaken for one produced
by a real model.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from dspy.clients.base_lm import BaseLM
from dspy.dsp.utils.utils import dotdict

#: Fields this double knows how to answer. Every other field in a prompt is
#: treated as an input.
_KNOWN_OUTPUTS = (
    "reasoning",
    "section_draft",
    "cited_chunk_ids",
    "cohesive_manuscript",
    "grounded_draft",
    "answer",
)

_FIELD_RE = re.compile(
    r"\[\[ ## (?P<name>[^#]+?) ## \]\]\n(?P<value>.*?)(?=\n\[\[ ## |$)",
    re.DOTALL,
)

_PASSAGE_RE = re.compile(r"\[#(?:CHUNK_ID:\s*)?(?P<chunk_id>[0-9a-zA-Z]+)\]")

_PASSAGE_START_RE = re.compile(r"^--- PASSAGE\s+\d+", re.MULTILINE)


def parse_prompt_fields(prompt: str) -> dict[str, str]:
    """Split a DSPy chat prompt into its ``{field: value}`` parts."""
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(prompt):
        name = match.group("name").strip()
        if name:
            fields[name] = match.group("value").strip()
    return fields


class StubLM(BaseLM):  # type: ignore[misc]
    """A language-model double that drafts grounded, citation-bearing text.

    The response always contains every field the known signatures can ask
    for, so the double works with any of them without per-signature
    configuration.
    """

    #: The name this provider reports itself under.
    model_name = "stub"

    def __init__(self, *, draft_prefix: str = "") -> None:
        super().__init__(self.model_name, "chat", 0.0, 1000, True)
        #: Extra text placed before the quoted passage in every draft.
        self.draft_prefix = draft_prefix
        #: The prompts this double was asked to answer, in order.
        self.calls: list[dict[str, str]] = []

    # -- drafting -------------------------------------------------------
    def _first_chunk(self, fields: dict[str, str]) -> tuple[str, str] | None:
        """The first passage of the assigned chunks and the id assigned to it.

        The drafter hands the model passages formatted as
        ``--- PASSAGE n [#CHUNK_ID: <id>] ---`` followed by the passage text,
        so the text of a passage is everything between its marker line and the
        next passage marker.
        """
        assigned = fields.get("assigned_chunks_text", "")
        match = _PASSAGE_RE.search(assigned)
        if match is None:
            return None
        chunk_id = match.group("chunk_id")
        remainder = assigned[match.end() :]
        next_passage = _PASSAGE_START_RE.search(remainder)
        if next_passage is not None:
            remainder = remainder[: next_passage.start()]
        # The prompt appends its own instructions after the passages, and those
        # contain bracketed text ("list[str]") that the citation grammar would
        # otherwise read as a chunk id. Bound the passage at the end of the
        # passage block.
        for boundary in ("\n\n\n", "\nRespond with the corresponding output fields"):
            index = remainder.find(boundary)
            if index != -1:
                remainder = remainder[:index]
        text = remainder.strip()
        if text.startswith("---"):
            # The marker was on its own header line: drop the remainder of it.
            text = text.split("\n", 1)[1].strip() if "\n" in text else ""
        return chunk_id, text

    def _section_draft(self, fields: dict[str, str]) -> tuple[str, list[str]]:
        """Build a draft that quotes the assigned passage and cites it.

        Every sentence carries its own citation marker, which is what the
        signature demands ("every factual proposition must carry an immediate
        inline citation tag") and what the claim extractor needs in order to
        attribute each claim to its chunk.
        """
        chunk = self._first_chunk(fields)
        if chunk is None:
            return ("", [])
        chunk_id, text = chunk
        if self.draft_prefix:
            text = f"{self.draft_prefix} {text}"
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            sentences = [text.strip()]
        body = " ".join(f"{sentence} [#{chunk_id}]" for sentence in sentences)
        return (body, [chunk_id])

    # -- response -------------------------------------------------------
    def _answers(self, fields: dict[str, str]) -> dict[str, Any]:
        draft, cited = self._section_draft(fields)
        full_draft = fields.get("full_draft", "")
        draft_text = fields.get("draft_text", "")
        return {
            "reasoning": (
                "The assigned passage states the figure directly, so the "
                "section restates it verbatim and cites its chunk id."
            ),
            "section_draft": draft,
            "cited_chunk_ids": cited,
            "cohesive_manuscript": full_draft or draft,
            "grounded_draft": (
                f"{draft_text} [{cited[0]}]" if draft_text and cited else draft_text
            ),
            # The notebook's answer is the same grounded text: the passage it
            # was given, quoted, with that passage's own chunk id. With no
            # passage there is nothing to ground an answer in, so the answer
            # is empty rather than invented.
            "answer": draft,
        }

    def _format(self, answers: dict[str, Any]) -> str:
        blocks: list[str] = []
        for name in _KNOWN_OUTPUTS:
            value = answers.get(name, "")
            if isinstance(value, list):
                rendered = "[" + ", ".join(f'"{v}"' for v in value) + "]"
            else:
                rendered = str(value)
            blocks.append(f"[[ ## {name} ## ]]\n{rendered}")
        return "\n".join(blocks)

    def forward(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Any:
        messages = messages or [{"role": "user", "content": prompt}]
        content = messages[-1]["content"]
        fields = parse_prompt_fields(content)
        self.calls.append(fields)
        answers = self._answers(fields)
        body = self._format(answers)
        message = dotdict(content=body, tool_calls=None)
        return dotdict(
            choices=[dotdict(message=message, finish_reason="stop")],
            usage=dotdict(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            model=self.model_name,
        )

    async def aforward(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Any:
        return self.forward(prompt=prompt, messages=messages, **kwargs)


__all__ = ["StubLM", "parse_prompt_fields"]
