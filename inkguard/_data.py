"""Generated data — word lists, name lists, vocabularies — kept as data.

The large lists the engine consults (English words, the name registry,
clinical vocabularies) are built by scripts in `tools/` from openly licensed
sources. They are stored as compressed JSON in `inkguard/data/`, one file
per list module, and each list module is a short file that loads its data
from there. Code stays code; data stays data, and both are covered by the
code fingerprint (fluorite), so a changed list is a changed version.

Two functions:

    load(name)          the symbols of one data file, decoded
    pack_module(name)   turn a generated list module written as Python
                        (what the build scripts produce) into its data file
                        and a short loader module

The JSON is written with sorted keys and members, and gzip with a fixed
timestamp, so the same list always gives the same bytes.
"""
from __future__ import annotations

import ast
import gzip
import json
from functools import lru_cache
from importlib import resources
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_DATA = _PACKAGE / "data"
FORMAT = 1


def _encode(value):
    if isinstance(value, frozenset):
        return {"t": "frozenset", "v": sorted((_encode(x) for x in value), key=_order)}
    if isinstance(value, set):
        return {"t": "set", "v": sorted((_encode(x) for x in value), key=_order)}
    if isinstance(value, tuple):
        return {"t": "tuple", "v": [_encode(x) for x in value]}
    if isinstance(value, list):
        return {"t": "list", "v": [_encode(x) for x in value]}
    if isinstance(value, dict):
        return {"t": "dict", "v": [[_encode(k), _encode(v)] for k, v in sorted(value.items(), key=lambda kv: _order(kv[0]))]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return {"t": "value", "v": value}
    raise TypeError(f"cannot store {type(value).__name__} as data")


def _order(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def _decode(node):
    t, v = node["t"], node["v"]
    if t == "value":
        return v
    if t == "frozenset":
        return frozenset(_decode(x) for x in v)
    if t == "set":
        return {_decode(x) for x in v}
    if t == "tuple":
        return tuple(_decode(x) for x in v)
    if t == "list":
        return [_decode(x) for x in v]
    if t == "dict":
        return {_decode(k): _decode(val) for k, val in v}
    raise ValueError(f"unknown data type {t!r}")


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """The symbols stored for list module `name`, decoded."""
    raw = resources.files("inkguard").joinpath("data", f"{name}.json.gz").read_bytes()
    doc = json.loads(gzip.decompress(raw).decode("utf-8"))
    if doc.get("format") != FORMAT:
        raise ValueError(f"data file {name}: unknown format {doc.get('format')!r}")
    return {k: _decode(v) for k, v in doc["symbols"].items()}


def _write_gz(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as gz:
            gz.write(payload)


def pack_module(name: str, built_by: str) -> Path:
    """Read the generated module `inkguard/<name>.py` (as the build scripts
    write it), store its public symbols in `inkguard/data/<name>.json.gz`,
    and replace the module with a short loader that keeps its docstring and
    its names. Returns the data file's path."""
    src_path = _PACKAGE / f"{name}.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    doc = ast.get_docstring(tree) or ""
    namespace: dict = {}
    exec(compile(tree, str(src_path), "exec"), namespace)       # noqa: S102 — our own generated file
    names = [n for n in namespace if n.isupper() and not n.startswith("_")]
    if not names:
        raise ValueError(f"{name}: no public symbols to store")
    symbols = {n: _encode(namespace[n]) for n in sorted(names)}
    payload = json.dumps({"format": FORMAT, "module": name, "built_by": built_by,
                          "symbols": symbols}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    out = _DATA / f"{name}.json.gz"
    _write_gz(out, payload)

    def kind(v) -> str:
        return {frozenset: "frozenset", set: "set", dict: "dict", tuple: "tuple",
                list: "list"}.get(type(v), type(v).__name__)

    lines = ['"""' + doc.replace('"""', "'''").rstrip() + "\n\n"
             f"The data is in inkguard/data/{name}.json.gz, written by {built_by}\n"
             'through inkguard._data.pack_module. Do not edit by hand."""',
             "from inkguard._data import load as _load", "",
             f"_D = _load({name!r})"]
    lines += [f"{n}: {kind(namespace[n])} = _D[{n!r}]" for n in sorted(names)]
    lines += ["del _D", ""]
    src_path.write_text("\n".join(lines), encoding="utf-8")
    return out
