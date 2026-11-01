"""The deterministic language-model double used by the test suite.

The implementation now lives in the shipped package
(:mod:`lmeditor.llm.stub`) so the application can also be run offline. This
module keeps the import path the tests use.
"""

from __future__ import annotations

from lmeditor.llm.stub import StubLM, parse_prompt_fields

__all__ = ["StubLM", "parse_prompt_fields"]
