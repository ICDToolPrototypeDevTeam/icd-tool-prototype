"""RPDU-specific profile hooks.

Currently a no-op — RPDU's HLR Excel rows are clean per-cell, no
content rewriting needed before the AI labeler / matcher.

Reserved for future RPDU-specific HLR pre-processing (e.g. cell merge
expansion, requirement content stitching across split rows).
"""