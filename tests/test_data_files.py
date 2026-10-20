"""The word lists are data, and the data is exact and reproducible."""
import gzip
import json

from inkguard import _data

LISTS = ("lexicon_data_v2", "lexicon_english", "lexicon_gate", "lexicon_icd_vocab",
         "lexicon_open", "lexicon_rare", "name_registry")


def test_every_list_loads_with_its_names():
    from inkguard.name_registry import NAMES
    from inkguard.lexicon_gate import ABBREVIATIONS
    assert "smith" in NAMES and len(NAMES) > 50_000
    assert isinstance(ABBREVIATIONS, frozenset) and len(ABBREVIATIONS) > 1_000
    for name in LISTS:
        assert _data.load(name), name


def test_types_survive_the_round_trip():
    sample = {"A": frozenset({"x", "y"}), "B": {"k": "v"}, "C": ("p", 1), "D": 7, "E": {"z"}}
    enc = {k: _data._encode(v) for k, v in sample.items()}
    assert {k: _data._decode(json.loads(json.dumps(v))) for k, v in enc.items()} == sample


def test_the_files_are_deterministic(tmp_path):
    """Gzip carries no timestamp and members are sorted, so the same list
    always gives the same bytes and the same code fingerprint."""
    for name in LISTS:
        raw = (_data._DATA / f"{name}.json.gz").read_bytes()
        assert raw[4:8] == b"\x00\x00\x00\x00", name           # gzip mtime field is zero
        doc = json.loads(gzip.decompress(raw))
        assert doc["format"] == _data.FORMAT and doc["module"] == name
