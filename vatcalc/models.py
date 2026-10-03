"""数据类型定义：税类、SKU 定义、调整条目、试算结果。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal


class TaxClass(enum.Enum):
    """税类：应税 / 零税率 / 免税。免税不等于零税率。"""

    TAXABLE = "taxable"
    ZERO_RATED = "zero_rated"
    EXEMPT = "exempt"


@dataclass(frozen=True)
class SkuDefinition:
    """单个 SKU 的完整定义。

    value_added: 自有加工增值额（元，精确到分）。
    tax_rate: 应税 SKU 的税率（如 Decimal("0.13")），其余为 None。
    recipe: 生产一单位该 SKU 所需的输入 SKU 及用量；原料为空。
    """

    value_added: Decimal
    tax_class: TaxClass
    tax_rate: Decimal | None = None
    recipe: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class AdjustEntry:
    """批量调整中的一条变更；为 None 的字段保持不变。"""

    sku: str
    value_added: Decimal | None = None
    tax_class: TaxClass | None = None
    tax_rate: Decimal | None = None
    recipe: dict[str, Decimal] | None = None


@dataclass(frozen=True)
class TrialResult:
    """单个 SKU 的试算结果，均已按分四舍五入。"""

    net_price: Decimal
    output_tax: Decimal
    deductible_input_tax: Decimal
