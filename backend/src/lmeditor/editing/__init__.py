"""Targeted inline editing of manuscript sections.

The package exposes the small-edit capability: rewrite one paragraph or one
section of a manuscript, grounded in the document's own sources, and refuse
the revision when the sources do not support it.

    result = await apply_inline_edit(request, section, index=index)

Everything the domain needs is in :mod:`lmeditor.editing.editor`, and the
language-model seam it calls is in :mod:`lmeditor.editing.rewrite`. This
package is a leaf: it imports the core contracts, the retrieval service, the
verifier and the language-model factory, and nothing imports it except the
routes that expose it.
"""

from lmeditor.editing.editor import InlineEditor, apply_inline_edit
from lmeditor.editing.rewrite import RewriteFn, default_rewrite

__all__ = [
    "InlineEditor",
    "RewriteFn",
    "apply_inline_edit",
    "default_rewrite",
]
