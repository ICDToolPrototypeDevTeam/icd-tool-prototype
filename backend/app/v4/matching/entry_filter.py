# -*- coding: utf-8 -*-
"""EoICD entry filter pipeline — removes noise entries before Case generation.

Extensible design: add new filter rules by appending to _FILTER_RULES.
Each rule is a function (EoICDRequirement) -> bool, where True = keep.

Current rules:
  1. Protocol overhead DataFormatType: A429 protocol stack fields that are not
     application-layer data (OCTLBL, PARITY, SDI, SSM_BNR).
"""

from __future__ import annotations

from app.v4.config import PROTOCOL_DATAFORMATS
from app.v4.models import EoICDRequirement


def _filter_protocol_dataformat(req: EoICDRequirement) -> bool:
    """Filter out A429 protocol overhead DataFormatType entries.

    A429OCTLBL (Label field), A429PARITY (parity bit), A429SDI (SDI field),
    and A429_SSM_BNR (SSM field) are protocol stack overhead, not
    application-layer data. They should not participate in reverse matching.
    """
    if req.attribute_name != "DataFormatType":
        return True
    if str(req.attribute_value).upper() in PROTOCOL_DATAFORMATS:
        return False
    return True


# Ordered list of filter rules (all must pass for an entry to be kept)
_FILTER_RULES = [
    _filter_protocol_dataformat,
]


def should_keep(req: EoICDRequirement) -> bool:
    """Check whether an EoICD entry should be kept for reverse matching.

    Returns False if any filter rule rejects the entry.
    """
    for rule in _FILTER_RULES:
        if not rule(req):
            return False
    return True
