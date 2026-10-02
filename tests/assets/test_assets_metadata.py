"""The sidecar: it round trips, and it refuses anything it does not recognise.

The loader is strict in both directions on purpose. A missing key means an older writer;
an unknown key means a newer one or a hand edit. Ignoring either is how an atlas and its
sidecar drift apart while every test still passes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from assets_helpers import shipped_metadata, synthetic_pack
from battle_city_tools.assets.errors import AssetInvalid
from battle_city_tools.assets.metadata import SCHEMA_VERSION, load, serialize, to_document


def _document() -> dict[str, Any]:
    metadata, _, _ = synthetic_pack()
    return json.loads(serialize(metadata).decode("utf-8"))


def _reload(document: dict[str, Any]) -> None:
    load(json.dumps(document).encode("utf-8"), artifact="fixture.json")


def test_a_sidecar_round_trips_through_the_loader() -> None:
    metadata, _, _ = synthetic_pack()
    assert load(serialize(metadata)) == metadata


def test_the_shipped_sidecar_re_serialises_to_the_bytes_on_disk() -> None:
    """The canonical encoding is the encoding in git, so a rebuild cannot churn the file."""
    from assets_helpers import SIDECAR_PATH

    assert serialize(shipped_metadata()) == SIDECAR_PATH.read_bytes()


def test_the_encoding_ends_with_a_newline_and_uses_two_space_indentation() -> None:
    metadata, _, _ = synthetic_pack()
    text = serialize(metadata).decode("utf-8")
    assert text.endswith("\n")
    assert '\n  "pack_id"' in text


def test_a_future_schema_version_is_refused_rather_than_guessed_at() -> None:
    document = _document()
    document["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(AssetInvalid, match="this reader understands version"):
        _reload(document)


def test_a_missing_top_level_key_is_refused_by_name() -> None:
    document = _document()
    del document["palette"]
    with pytest.raises(AssetInvalid, match="missing key\\(s\\): palette"):
        _reload(document)


def test_an_unknown_top_level_key_is_refused_by_name() -> None:
    document = _document()
    document["mood"] = "cheerful"
    with pytest.raises(AssetInvalid, match="unknown key\\(s\\): mood"):
        _reload(document)


def test_an_unknown_render_setting_is_refused() -> None:
    document = _document()
    document["render"]["cycles.vibes"] = 1
    with pytest.raises(AssetInvalid, match="render"):
        _reload(document)


def test_a_missing_render_setting_is_refused() -> None:
    document = _document()
    del document["render"]["cycles.seed"]
    with pytest.raises(AssetInvalid, match="cycles.seed"):
        _reload(document)


def test_a_render_setting_that_is_not_a_scalar_is_refused() -> None:
    document = _document()
    document["render"]["cycles.seed"] = [0]
    with pytest.raises(AssetInvalid, match="string, number or boolean"):
        _reload(document)


def test_a_digest_that_is_not_hexadecimal_is_refused() -> None:
    document = _document()
    document["atlas"]["sha256_pixels"] = "z" * 64
    with pytest.raises(AssetInvalid, match="hexadecimal"):
        _reload(document)


def test_a_short_digest_is_refused() -> None:
    document = _document()
    document["frames"][0]["sha256_pixels"] = "abcd"
    with pytest.raises(AssetInvalid, match="hexadecimal"):
        _reload(document)


def test_a_repeated_frame_name_is_refused() -> None:
    document = _document()
    document["frames"][1]["name"] = document["frames"][0]["name"]
    with pytest.raises(AssetInvalid, match="appears twice"):
        _reload(document)


def test_a_repeated_palette_colour_is_refused() -> None:
    document = _document()
    document["palette"][1]["rgb"] = document["palette"][0]["rgb"]
    with pytest.raises(AssetInvalid, match="appears twice"):
        _reload(document)


def test_a_palette_channel_outside_the_byte_range_is_refused() -> None:
    document = _document()
    document["palette"][0]["rgb"] = [0, 0, 300]
    with pytest.raises(AssetInvalid, match="0..255"):
        _reload(document)


def test_a_rectangle_without_four_numbers_is_refused() -> None:
    document = _document()
    document["frames"][0]["rect"] = [0, 0, 16]
    with pytest.raises(AssetInvalid, match="four whole numbers"):
        _reload(document)


def test_a_boolean_is_not_accepted_where_a_number_belongs() -> None:
    document = _document()
    document["frames"][0]["pivot"] = [True, 0]
    with pytest.raises(AssetInvalid, match="whole number"):
        _reload(document)


def test_a_one_frame_animation_is_refused() -> None:
    document = _document()
    document["animations"][0]["frames"] = ["alpha"]
    with pytest.raises(AssetInvalid, match="at least two frames"):
        _reload(document)


def test_a_readability_sign_outside_minus_one_and_one_is_refused() -> None:
    document = _document()
    document["readability"]["quadrant_sign"] = [["alpha", 0, 10]]
    with pytest.raises(AssetInvalid, match="must be -1 or 1"):
        _reload(document)


def test_a_malformed_readability_rule_is_refused() -> None:
    document = _document()
    document["readability"]["luma_contrast"] = [["alpha", 4]]
    with pytest.raises(AssetInvalid, match="frame name, frame name, threshold"):
        _reload(document)


def test_a_document_that_is_not_json_is_refused() -> None:
    with pytest.raises(AssetInvalid, match="not valid JSON"):
        load(b"{ this is not json")


def test_a_document_that_is_not_utf8_is_refused() -> None:
    with pytest.raises(AssetInvalid, match="not valid UTF-8"):
        load(b"\xff\xfe{}")


def test_a_json_array_at_the_top_level_is_refused() -> None:
    with pytest.raises(AssetInvalid, match="must be a JSON object"):
        load(b"[]")


def test_a_diagnostic_names_the_artifact_and_the_field() -> None:
    document = _document()
    del document["palette"]
    with pytest.raises(AssetInvalid) as caught:
        load(json.dumps(document).encode("utf-8"), artifact="packs/mine.json")
    assert caught.value.diagnostic.artifact == "packs/mine.json"
    assert "palette" in str(caught.value)


def test_the_document_builder_and_the_serialiser_agree() -> None:
    metadata, _, _ = synthetic_pack()
    assert json.loads(serialize(metadata).decode("utf-8")) == to_document(metadata)
