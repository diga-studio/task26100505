"""类型定义：税类、SKU 定义、批次调整条目、试算结果。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal


class TaxCategory(enum.Enum):
    """税类。免税与零税率不同：免税 SKU 的进项税计入成本且不可抵扣。"""

    TAXABLE = "taxable"      # 应税，设有税率
    ZERO_RATED = "zero"      # 零税率，销项为零但进项可抵扣
    EXEMPT = "exempt"        # 免税，进项计入成本且不可抵扣


@dataclass(frozen=True)
class BomItem:
    """配方条目：生产一单位某 SKU 所需的一个输入及其用量。"""

    sku: str
    quantity: Decimal


@dataclass(frozen=True)
class SkuDefinition:
    """SKU 定义：自有加工增值额（元，精确到分）、税类、税率（仅应税）、配方。"""

    sku: str
    value_added: Decimal
    tax_category: TaxCategory
    tax_rate: Decimal | None = None
    bom: tuple[BomItem, ...] = ()


@dataclass(frozen=True)
class Adjustment:
    """批次中的一条调整：可改变加工增值额、税类、税率或完整配方。

    值为 None 表示该项不变；配方用空元组表示清空配方。
    """

    sku: str
    value_added: Decimal | None = None
    tax_category: TaxCategory | None = None
    tax_rate: Decimal | None = None
    bom: tuple[BomItem, ...] | None = None


@dataclass(frozen=True)
class TrialResult:
    """单个 SKU 的试算结果，均为十进制、四舍五入到分。"""

    sku: str
    net_price: Decimal            # 未税试算价
    output_tax: Decimal           # 销项税额
    deductible_input_tax: Decimal  # 可抵扣进项税额
