"""A small JSON Schema validator for the checked-in content schemas.

Content is validated against a schema document that ships with this package, so the
contract is reviewable data rather than hand-written Python branches. The validator
implements the closed subset of JSON Schema 2020-12 that those schemas use and rejects
anything outside it at compile time, which keeps the two from drifting: a schema that
reaches for an unimplemented keyword fails loudly instead of silently validating nothing.

Deliberate restrictions
-----------------------
* ``$ref`` resolves only inside the same document, under ``#/$defs/``. Nothing here
  fetches a remote schema, so validation never touches the network.
* ``additionalProperties`` accepts only ``false``. Every object in a content schema is
  closed, so an unknown field is an error rather than ignored data.
* ``type: "integer"`` accepts only a JSON integer. ``true`` is not an integer even though
  Python says ``isinstance(True, int)``, and ``1.0`` is rejected rather than coerced.
* ``const`` and ``enum`` compare the same way: ``1``, ``1.0`` and ``true`` are three
  different values here, where JSON Schema would call the first two equal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

from .errors import ContentSchemaError, ContentValidationError
from .jsonio import JsonValue

_ANNOTATION_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"$schema", "$id", "$comment", "title", "description"}
)

_CONTAINER_KEYWORDS: Final[frozenset[str]] = frozenset({"$defs"})
"""Keywords that hold other schemas rather than asserting anything about an instance."""

_ASSERTION_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "$ref",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "pattern",
    }
)

SUPPORTED_KEYWORDS: Final[frozenset[str]] = (
    _ANNOTATION_KEYWORDS | _ASSERTION_KEYWORDS | _CONTAINER_KEYWORDS
)
"""Every keyword a checked-in content schema may use."""

_JSON_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)

_LOCAL_REF_PREFIX: Final[str] = "#/$defs/"


@dataclass(frozen=True, slots=True)
class CompiledSchema:
    """A validated schema document, ready to check instances against."""

    name: str
    root: Mapping[str, JsonValue]
    defs: Mapping[str, Mapping[str, JsonValue]]
    patterns: Mapping[str, re.Pattern[str]]

    def validate(self, instance: JsonValue, *, path: Path) -> None:
        """Raise :class:`ContentValidationError` for the first violation in ``instance``.

        ``path`` is only used to name the file in the error; nothing is read from it.
        """
        self._check(self.root, instance, field="", path=path)

    def _check(
        self,
        node: Mapping[str, JsonValue],
        instance: JsonValue,
        *,
        field: str,
        path: Path,
    ) -> None:
        reference = node.get("$ref")
        if isinstance(reference, str):
            self._check(
                self.defs[reference[len(_LOCAL_REF_PREFIX) :]], instance, field=field, path=path
            )
            return

        expected_type = node.get("type")
        if isinstance(expected_type, str) and not _has_type(instance, expected_type):
            _fail(path, field, f"must be {_article(expected_type)}, found {_type_name(instance)}")

        if "const" in node:
            expected = node["const"]
            if not _json_equal(instance, expected):
                _fail(path, field, f"must be {_render(expected)}")

        allowed = node.get("enum")
        if isinstance(allowed, list) and not any(
            _json_equal(instance, option) for option in allowed
        ):
            rendered = ", ".join(_render(option) for option in allowed)
            _fail(path, field, f"must be one of [{rendered}]")

        if isinstance(instance, str):
            self._check_string(node, instance, field=field, path=path)
        elif isinstance(instance, bool):
            pass
        elif isinstance(instance, int | float):
            _check_number(node, instance, field=field, path=path)
        elif isinstance(instance, list):
            self._check_array(node, instance, field=field, path=path)
        elif isinstance(instance, dict):
            self._check_object(node, instance, field=field, path=path)

    def _check_string(
        self, node: Mapping[str, JsonValue], instance: str, *, field: str, path: Path
    ) -> None:
        minimum = node.get("minLength")
        if isinstance(minimum, int) and len(instance) < minimum:
            _fail(path, field, f"must have at least {minimum} character(s)")
        maximum = node.get("maxLength")
        if isinstance(maximum, int) and len(instance) > maximum:
            _fail(path, field, f"must have at most {maximum} character(s)")
        pattern = node.get("pattern")
        if isinstance(pattern, str) and not self.patterns[pattern].search(instance):
            _fail(path, field, f"must match {pattern}")

    def _check_array(
        self,
        node: Mapping[str, JsonValue],
        instance: Sequence[JsonValue],
        *,
        field: str,
        path: Path,
    ) -> None:
        minimum = node.get("minItems")
        if isinstance(minimum, int) and len(instance) < minimum:
            _fail(path, field, f"must have at least {minimum} item(s), found {len(instance)}")
        maximum = node.get("maxItems")
        if isinstance(maximum, int) and len(instance) > maximum:
            _fail(path, field, f"must have at most {maximum} item(s), found {len(instance)}")
        items = node.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                self._check(items, item, field=f"{field}[{index}]", path=path)

    def _check_object(
        self,
        node: Mapping[str, JsonValue],
        instance: Mapping[str, JsonValue],
        *,
        field: str,
        path: Path,
    ) -> None:
        properties = node.get("properties")
        declared: Mapping[str, JsonValue] = properties if isinstance(properties, dict) else {}

        required = node.get("required")
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in instance:
                    _fail(path, _child(field, name), "is required")

        if node.get("additionalProperties") is False:
            for name in sorted(set(instance) - set(declared)):
                _fail(path, _child(field, name), "is not a known field")

        # Schema order, not instance order: two documents with the same fields in a
        # different order must fail on the same field.
        for name, subschema in declared.items():
            if name in instance and isinstance(subschema, dict):
                self._check(subschema, instance[name], field=_child(field, name), path=path)


def compile_schema(document: Mapping[str, JsonValue], *, name: str) -> CompiledSchema:
    """Check that ``document`` stays inside the supported subset and precompile it.

    Raises :class:`ContentSchemaError`; a failure here is a defect in this package's own
    schema files, never in the content being loaded.
    """
    raw_defs = document.get("$defs")
    defs: dict[str, Mapping[str, JsonValue]] = {}
    if raw_defs is not None:
        if not isinstance(raw_defs, dict):
            raise ContentSchemaError(f"{name}: $defs must be an object")
        for key, value in raw_defs.items():
            if not isinstance(value, dict):
                raise ContentSchemaError(f"{name}: $defs.{key} must be an object")
            defs[key] = value

    patterns: dict[str, re.Pattern[str]] = {}
    _compile_node(document, name=name, location="#", defs=defs, patterns=patterns)
    for key, definition in defs.items():
        _compile_node(
            definition, name=name, location=f"#/$defs/{key}", defs=defs, patterns=patterns
        )
    return CompiledSchema(name=name, root=document, defs=defs, patterns=patterns)


def _compile_node(
    node: Mapping[str, JsonValue],
    *,
    name: str,
    location: str,
    defs: Mapping[str, Mapping[str, JsonValue]],
    patterns: dict[str, re.Pattern[str]],
) -> None:
    unsupported = sorted(set(node) - SUPPORTED_KEYWORDS)
    if unsupported:
        raise ContentSchemaError(
            f"{name}: {location} uses unsupported keyword(s): {', '.join(unsupported)}"
        )

    reference = node.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith(_LOCAL_REF_PREFIX):
            raise ContentSchemaError(
                f"{name}: {location} must reference {_LOCAL_REF_PREFIX}<name>; "
                f"remote references are not supported"
            )
        target = reference[len(_LOCAL_REF_PREFIX) :]
        if target not in defs:
            raise ContentSchemaError(f"{name}: {location} references unknown $defs/{target}")
        siblings = sorted(set(node) & (_ASSERTION_KEYWORDS - {"$ref"}))
        if siblings:
            # The validator follows the reference and returns, so a sibling assertion
            # would be silently dropped. Reject it instead of validating less than the
            # schema appears to say.
            raise ContentSchemaError(
                f"{name}: {location} may not combine $ref with {', '.join(siblings)}"
            )

    declared_type = node.get("type")
    if declared_type is not None and (
        not isinstance(declared_type, str) or declared_type not in _JSON_TYPE_NAMES
    ):
        raise ContentSchemaError(f"{name}: {location} declares an unknown type {declared_type!r}")

    extra = node.get("additionalProperties")
    if extra is not None and extra is not False:
        raise ContentSchemaError(f"{name}: {location} may only set additionalProperties to false")

    pattern = node.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise ContentSchemaError(f"{name}: {location} declares a non-string pattern")
        try:
            patterns[pattern] = re.compile(pattern)
        except re.error as error:
            raise ContentSchemaError(
                f"{name}: {location} declares an invalid pattern {pattern!r}: {error}"
            ) from error

    properties = node.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise ContentSchemaError(f"{name}: {location}.properties must be an object")
        for key, value in properties.items():
            if not isinstance(value, dict):
                raise ContentSchemaError(f"{name}: {location}.properties.{key} must be an object")
            _compile_node(
                value,
                name=name,
                location=f"{location}/properties/{key}",
                defs=defs,
                patterns=patterns,
            )

    items = node.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise ContentSchemaError(f"{name}: {location}.items must be an object")
        _compile_node(items, name=name, location=f"{location}/items", defs=defs, patterns=patterns)


def _fail(path: Path, field: str, message: str) -> NoReturn:
    raise ContentValidationError(path=path, field=field, message=message)


def _child(field: str, name: str) -> str:
    return f"{field}.{name}" if field else name


def _has_type(instance: JsonValue, expected: str) -> bool:
    match expected:
        case "object":
            return isinstance(instance, dict)
        case "array":
            return isinstance(instance, list)
        case "string":
            return isinstance(instance, str)
        case "integer":
            return isinstance(instance, int) and not isinstance(instance, bool)
        case "number":
            return isinstance(instance, int | float) and not isinstance(instance, bool)
        case "boolean":
            return isinstance(instance, bool)
        case _:
            return instance is None


def _type_name(instance: JsonValue) -> str:
    if instance is None:
        return "null"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, list):
        return "array"
    return "object"


def _article(type_name: str) -> str:
    return f"an {type_name}" if type_name[0] in "aeiou" else f"a {type_name}"


def _json_equal(left: JsonValue, right: JsonValue) -> bool:
    """Compare two JSON values without Python's ``True == 1`` or ``1.0 == 1`` equivalence.

    JSON Schema calls ``1`` and ``1.0`` equal numbers. This validator does not, for the
    same reason ``type: "integer"`` rejects ``1.0``: a version field that decodes to a
    float is not the integer the loader is about to read, and accepting it here turns a
    content error into an internal one further down.
    """
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, float) != isinstance(right, float):
        return False
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(one, other) for one, other in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def _render(value: JsonValue) -> str:
    """Render a schema literal the way the document spells it."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _check_number(
    node: Mapping[str, JsonValue], instance: int | float, *, field: str, path: Path
) -> None:
    minimum = node.get("minimum")
    if isinstance(minimum, int | float) and not isinstance(minimum, bool) and instance < minimum:
        _fail(path, field, f"must be at least {_render(minimum)}, found {_render(instance)}")
    maximum = node.get("maximum")
    if isinstance(maximum, int | float) and not isinstance(maximum, bool) and instance > maximum:
        _fail(path, field, f"must be at most {_render(maximum)}, found {_render(instance)}")
