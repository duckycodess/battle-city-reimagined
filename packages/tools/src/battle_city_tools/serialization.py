"""Turn a draft into the exact bytes that will be validated and written.

One rule governs this module: a document is serialised **once**, and the bytes that
come out are the bytes that get validated and the bytes that get written. Serialising
twice -- once to check and once to store -- means the thing on disk was never the thing
that passed, and the two can differ for reasons as small as a dictionary that was
rebuilt in another order. Every writer in this package therefore takes a ``bytes``
payload rather than a document.

The encoding is deliberately plain: two-space indentation, keys in the order the builder
wrote them, a trailing newline, UTF-8. It matches the bundled packs, so a level exported
by these tools and a level checked into ``battle_city_content`` read the same way in a
diff.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
"""The value tree a content document is made of.

Declared here rather than imported from the content package's decoding module: that
module is the loader's own input hardening, and these tools are a writer. The two names
describe the same shape on purpose and are checked against each other every time a
serialised document is handed back to the loader.
"""

JSON_INDENT: Final[int] = 2
"""Indentation width, matching the checked-in packs."""


def serialize_document(document: Mapping[str, JsonValue]) -> bytes:
    """Encode ``document`` as the UTF-8 bytes of a content file.

    ``allow_nan`` is off because the content loader rejects the ``NaN`` and ``Infinity``
    literals that ``json`` would otherwise emit happily; failing here names the tool that
    produced the value instead of failing later against a file nobody wrote by hand.
    """
    text = json.dumps(
        dict(document),
        indent=JSON_INDENT,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=False,
    )
    return f"{text}\n".encode()
