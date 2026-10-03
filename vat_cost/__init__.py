"""多层物料增值税成本试算库（进程内数据）。

用法::

    from vat_cost import VatCostEngine, SkuDefinition, TaxCategory, BomItem

    engine = VatCostEngine()
    engine.load([...])
    engine.adjust([...])
    result = engine.query("FINISHED")
"""
from .engine import VatCostEngine
from .errors import (
    CycleError,
    InvalidDefinitionError,
    UnknownSkuError,
    VatCostError,
)
from .types import Adjustment, BomItem, SkuDefinition, TaxCategory, TrialResult

__all__ = [
    "Adjustment",
    "BomItem",
    "CycleError",
    "InvalidDefinitionError",
    "SkuDefinition",
    "TaxCategory",
    "TrialResult",
    "UnknownSkuError",
    "VatCostEngine",
    "VatCostError",
]
