"""Mined clinical vocabulary, second pass — GENERATED FILE.

Every term comes from FDA GUDID, FDA drug listings or ICD-10-CM (all
public domain). Per-term sources were recorded at build time and are
kept in tools/lexmine/lexicon_data_v2_sources.csv.gz. Terminology only: no
sentences, definitions or passages from any source are reproduced here.

SAFETY: every eponym harvested in this pass is AMBIGUOUS by
construction. The harvest cannot distinguish an eponym from a
surname, and a wrongly-unambiguous term leaks patient names
silently. Ambiguous terms still count as clinical when a disease
qualifier follows.

The data is in inkguard/data/lexicon_data_v2.json.gz, written by tools/lexmine/integrate.py
through inkguard._data.pack_module. Do not edit by hand."""
from inkguard._data import load as _load

_D = _load('lexicon_data_v2')
MINED_V2_AMBIGUOUS: set = _D['MINED_V2_AMBIGUOUS']
MINED_V2_UNAMBIGUOUS: set = _D['MINED_V2_UNAMBIGUOUS']
del _D
