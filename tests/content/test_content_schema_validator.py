"""The schema validator itself: a closed subset, checked at compile time.

The content contract is a checked-in JSON Schema, so the validator that reads it is part
of that contract. A schema reaching for a keyword this subset does not implement must
fail loudly rather than silently validate less than it appears to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from battle_city_content import ContentSchemaError, ContentValidationError, bundled_content_root
from battle_city_content.loader import LEVEL_SCHEMA_FILENAME, PACK_SCHEMA_FILENAME
from battle_city_content.schema import SUPPORTED_KEYWORDS, CompiledSchema, compile_schema

DOCUMENT = Path("in-memory.json")


def compiled(schema: dict[str, Any]) -> CompiledSchema:
    return compile_schema(schema, name="test.schema.json")


def reject(schema: dict[str, Any], instance: Any) -> ContentValidationError:
    with pytest.raises(ContentValidationError) as caught:
        compiled(schema).validate(instance, path=DOCUMENT)
    return caught.value


def accept(schema: dict[str, Any], instance: Any) -> None:
    compiled(schema).validate(instance, path=DOCUMENT)


@pytest.mark.parametrize(
    ("schema", "reason"),
    [
        ({"type": "object", "oneOf": []}, "unsupported keyword(s): oneOf"),
        ({"type": "object", "patternProperties": {}}, "unsupported keyword(s): patternProperties"),
        ({"$ref": "https://example.invalid/a.json"}, "remote references are not supported"),
        ({"$ref": "other.json#/$defs/a"}, "remote references are not supported"),
        ({"$ref": "#/$defs/missing"}, "references unknown $defs/missing"),
        ({"type": "widget"}, "declares an unknown type"),
        ({"type": ["string", "null"]}, "declares an unknown type"),
        ({"additionalProperties": {"type": "string"}}, "additionalProperties to false"),
        ({"additionalProperties": True}, "additionalProperties to false"),
        ({"pattern": "["}, "declares an invalid pattern"),
        ({"pattern": 4}, "declares a non-string pattern"),
        ({"properties": []}, "properties must be an object"),
        ({"properties": {"a": "string"}}, "properties.a must be an object"),
        ({"items": []}, "items must be an object"),
        ({"$defs": []}, "$defs must be an object"),
        ({"$defs": {"a": 1}}, "$defs.a must be an object"),
    ],
)
def test_a_schema_outside_the_subset_is_refused(schema: dict[str, Any], reason: str) -> None:
    with pytest.raises(ContentSchemaError) as caught:
        compiled(schema)
    assert reason in str(caught.value)


def test_a_reference_may_not_carry_sibling_assertions() -> None:
    schema = {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a", "minLength": 3}
    with pytest.raises(ContentSchemaError) as caught:
        compiled(schema)
    assert "may not combine $ref with minLength" in str(caught.value)


def test_nested_schemas_are_compiled_too() -> None:
    schema = {"type": "object", "properties": {"a": {"items": {"type": "widget"}}}}
    with pytest.raises(ContentSchemaError) as caught:
        compiled(schema)
    assert "#/properties/a/items" in str(caught.value)


def test_annotations_are_allowed_and_ignored() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.invalid/a.json",
        "$comment": "note",
        "title": "A",
        "description": "B",
        "type": "string",
    }
    accept(schema, "text")
    assert reject(schema, 4).message == "must be a string, found integer"


@pytest.mark.parametrize(
    ("expected", "value"),
    [
        ("integer", 4),
        ("number", 4),
        ("number", 4.5),
        ("string", "a"),
        ("boolean", True),
        ("array", []),
        ("object", {}),
        ("null", None),
    ],
)
def test_a_matching_type_is_accepted(expected: str, value: Any) -> None:
    accept({"type": expected}, value)


@pytest.mark.parametrize(
    ("expected", "value", "found"),
    [
        ("integer", True, "boolean"),
        ("integer", 1.0, "number"),
        ("integer", "1", "string"),
        ("number", True, "boolean"),
        ("string", 1, "integer"),
        ("boolean", 1, "integer"),
        ("object", [], "array"),
        ("array", {}, "object"),
        ("null", 0, "integer"),
    ],
)
def test_a_mismatched_type_names_what_it_found(expected: str, value: Any, found: str) -> None:
    error = reject({"type": expected}, value)
    assert error.message.endswith(f"found {found}")
    assert error.field == ""


@pytest.mark.parametrize("value", [1.0, True, "1", None, [1], {"a": 1}])
def test_const_does_not_coerce(value: Any) -> None:
    # 1, 1.0 and true are three different values here. Accepting a float where the
    # loader will read an integer turns a content error into an internal one.
    assert reject({"const": 1}, value).message == "must be 1"


@pytest.mark.parametrize("value", [1, "a", True])
def test_enum_does_not_coerce(value: Any) -> None:
    accept({"enum": [1, "a", True]}, value)
    assert reject({"enum": [1, "a"]}, 1.0).message == 'must be one of [1, "a"]'


def test_required_fields_are_reported_in_schema_order() -> None:
    schema = {"type": "object", "required": ["first", "second"]}
    error = reject(schema, {})
    assert error.field == "first"
    assert error.message == "is required"


def test_required_is_checked_before_unknown_fields() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {"a": {"type": "string"}},
    }
    assert reject(schema, {"b": 1}).field == "a"
    assert reject(schema, {"a": "x", "b": 1}).field == "b"
    assert reject(schema, {"a": "x", "b": 1}).message == "is not a known field"


def test_unknown_fields_are_reported_in_a_stable_order() -> None:
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    assert reject(schema, {"zeta": 1, "alpha": 2}).field == "alpha"


@pytest.mark.parametrize(
    ("schema", "instance", "message"),
    [
        ({"minItems": 2}, [1], "must have at least 2 item(s), found 1"),
        ({"maxItems": 1}, [1, 2], "must have at most 1 item(s), found 2"),
        ({"minLength": 2}, "a", "must have at least 2 character(s)"),
        ({"maxLength": 1}, "ab", "must have at most 1 character(s)"),
        ({"minimum": 1}, 0, "must be at least 1, found 0"),
        ({"maximum": 1}, 2, "must be at most 1, found 2"),
        ({"pattern": "^a+$"}, "ab", "must match ^a+$"),
    ],
)
def test_a_bound_reports_what_it_required(
    schema: dict[str, Any], instance: Any, message: str
) -> None:
    assert reject(schema, instance).message == message


def test_field_paths_name_nested_positions() -> None:
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {"type": "object", "properties": {"x": {"type": "integer"}}},
            }
        },
    }
    error = reject(schema, {"rows": [{"x": 1}, {"x": "no"}]})
    assert error.field == "rows[1].x"
    assert error.path == DOCUMENT


def test_a_local_reference_is_resolved() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/cell"}},
        "$defs": {"cell": {"type": "integer", "minimum": 0}},
    }
    accept(schema, {"a": 3})
    error = reject(schema, {"a": -1})
    assert error.field == "a"
    assert error.message == "must be at least 0, found -1"


def test_a_trailing_dollar_anchors_the_end_of_the_string() -> None:
    """``$`` in a schema means the end of the value, not "before a final newline".

    Python's ``$`` matches at either position, and the validator applies patterns with
    ``search``, so an identifier pattern that reads as if it forbade a newline would
    otherwise accept one riding along at the end of the string.
    """
    schema = {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"}
    accept(schema, "classic-01")
    assert reject(schema, "classic-01\n").message == "must match ^[a-z0-9]+(?:-[a-z0-9]+)*$"
    assert reject(schema, "\nclassic-01").field == ""
    assert reject(schema, "classic-01\nclassic-02").field == ""


@pytest.mark.parametrize("value", ["aaa\n", "\naaa", "aaa\nbbb", "aaa\r\n", "aaa ", "aaa\x00"])
def test_no_trailing_character_slips_past_an_end_anchor(value: str) -> None:
    assert reject({"pattern": "^[a-z]+$"}, value).message == "must match ^[a-z]+$"


def test_an_unanchored_pattern_still_matches_anywhere() -> None:
    # JSON Schema specifies search semantics, and translating the end anchor must not
    # quietly turn every pattern into a full match.
    accept({"pattern": "b"}, "abc")
    accept({"pattern": "b"}, "b\n")
    assert reject({"pattern": "b"}, "acd").message == "must match b"


def test_a_start_anchor_alone_is_left_alone() -> None:
    accept({"pattern": "^ab"}, "abc")
    assert reject({"pattern": "^ab"}, "xabc").message == "must match ^ab"


@pytest.mark.parametrize("pattern", ["^a$|^b$", "^a$b", "^(a$)"])
def test_a_dollar_before_the_end_is_refused_at_compile_time(pattern: str) -> None:
    # Each of these carries the same before-a-final-newline looseness, and none has a
    # translation that preserves what it was written to mean, so the schema is refused
    # rather than compiled into something weaker than it reads.
    with pytest.raises(ContentSchemaError) as caught:
        compiled({"pattern": pattern})
    assert "with '$' before its end" in str(caught.value)


@pytest.mark.parametrize("pattern", [r"^\$[0-9]+$", "^[$][0-9]+$"])
def test_a_literal_dollar_sign_is_not_an_anchor(pattern: str) -> None:
    accept({"pattern": pattern}, "$25")
    assert reject({"pattern": pattern}, "$25\n").field == ""


def test_every_end_anchored_pattern_in_the_checked_in_schemas_rejects_a_newline() -> None:
    """The schemas this package ships must not accept a trailing newline anywhere."""
    seen = 0
    for filename in (LEVEL_SCHEMA_FILENAME, PACK_SCHEMA_FILENAME):
        path = bundled_content_root() / "schemas" / filename
        schema = compile_schema(json.loads(path.read_text(encoding="utf-8")), name=filename)
        for pattern, expression in schema.patterns.items():
            if not pattern.endswith("$"):
                continue
            seen += 1
            assert expression.pattern.endswith("\\Z")
            assert expression.search("a\n") is None
    assert seen > 0


@pytest.mark.parametrize("filename", [LEVEL_SCHEMA_FILENAME, PACK_SCHEMA_FILENAME])
def test_the_checked_in_schemas_compile(filename: str) -> None:
    path = bundled_content_root() / "schemas" / filename
    document = json.loads(path.read_text(encoding="utf-8"))
    compile_schema(document, name=filename)


@pytest.mark.parametrize("filename", [LEVEL_SCHEMA_FILENAME, PACK_SCHEMA_FILENAME])
def test_the_checked_in_schemas_use_only_supported_keywords(filename: str) -> None:
    path = bundled_content_root() / "schemas" / filename
    document = json.loads(path.read_text(encoding="utf-8"))
    used: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            used.update(key for key in node if isinstance(key, str))
            for value in node.values():
                walk(value)

    walk(document)
    # Property names and $defs names are collected too, so this asserts containment of
    # the keywords rather than equality with the whole key set.
    assert SUPPORTED_KEYWORDS & used
    assert not (used & {"oneOf", "anyOf", "allOf", "not", "if", "then", "else", "$dynamicRef"})


@pytest.mark.parametrize("filename", [LEVEL_SCHEMA_FILENAME, PACK_SCHEMA_FILENAME])
def test_the_checked_in_schemas_declare_no_remote_reference(filename: str) -> None:
    text = (bundled_content_root() / "schemas" / filename).read_text(encoding="utf-8")
    document = json.loads(text)
    references: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str):
                references.append(reference)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    assert all(reference.startswith("#/$defs/") for reference in references)
