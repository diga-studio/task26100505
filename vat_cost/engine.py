"""试算引擎：整批载入、批量调整、单 SKU 试算查询。数据仅保存在进程内。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from .errors import CycleError, InvalidDefinitionError, UnknownSkuError
from .types import Adjustment, BomItem, SkuDefinition, TaxCategory, TrialResult

CENT = Decimal("0.01")
ZERO = Decimal("0")


def _to_decimal(value, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise InvalidDefinitionError(f"{field_name} 不是合法十进制数: {value!r}") from exc
    if not result.is_finite():
        raise InvalidDefinitionError(f"{field_name} 必须是有限十进制数: {value!r}")
    return result


def _round_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _normalize_bom(bom: Iterable) -> tuple[BomItem, ...]:
    items = []
    for entry in bom:
        if isinstance(entry, BomItem):
            sku, quantity = entry.sku, entry.quantity
        else:
            sku, quantity = entry
        items.append(BomItem(sku=str(sku), quantity=_to_decimal(quantity, "用量")))
    return tuple(items)


class VatCostEngine:
    """多层物料增值税成本试算引擎。"""

    def __init__(self) -> None:
        self._skus: dict[str, SkuDefinition] = {}
        self._cache: dict[str, TrialResult] = {}

    # ------------------------------------------------------------------ 载入

    def load(self, definitions: Iterable[SkuDefinition]) -> None:
        """整批载入，替换现有全部数据。任一定义非法或配方成环则整体不生效。"""
        new_skus: dict[str, SkuDefinition] = {}
        for definition in definitions:
            normalized = self._normalize_definition(definition)
            if normalized.sku in new_skus:
                raise InvalidDefinitionError(f"SKU 重复定义: {normalized.sku}")
            new_skus[normalized.sku] = normalized
        self._validate_graph(new_skus)
        self._skus = new_skus
        self._cache = {}

    # ------------------------------------------------------------------ 调整

    def adjust(self, adjustments: Iterable[Adjustment]) -> None:
        """批量调整：按批次最终状态整体生效，与条目顺序无关。

        最终配方成环或任一调整非法时整批不生效，原有数据不变。
        """
        new_skus = dict(self._skus)
        for adjustment in adjustments:
            new_skus[adjustment.sku] = self._apply_adjustment(new_skus, adjustment)
        self._validate_graph(new_skus)
        self._skus = new_skus
        self._cache = {}

    def _apply_adjustment(
        self, skus: dict[str, SkuDefinition], adjustment: Adjustment
    ) -> SkuDefinition:
        base = skus.get(adjustment.sku)
        if base is None:
            raise UnknownSkuError(f"调整目标 SKU 不存在: {adjustment.sku}")
        value_added = (
            base.value_added
            if adjustment.value_added is None
            else _to_decimal(adjustment.value_added, "加工增值额")
        )
        tax_category = (
            base.tax_category
            if adjustment.tax_category is None
            else TaxCategory(adjustment.tax_category)
        )
        tax_rate = (
            base.tax_rate
            if adjustment.tax_rate is None
            else _to_decimal(adjustment.tax_rate, "税率")
        )
        bom = base.bom if adjustment.bom is None else _normalize_bom(adjustment.bom)
        candidate = SkuDefinition(
            sku=base.sku,
            value_added=value_added,
            tax_category=tax_category,
            tax_rate=tax_rate,
            bom=bom,
        )
        return self._check_definition(candidate)

    # ------------------------------------------------------------------ 查询

    def query(self, sku: str) -> TrialResult:
        """查询单个 SKU 的试算结果。"""
        if sku not in self._skus:
            raise UnknownSkuError(f"SKU 不存在: {sku}")
        return self._compute(sku, {})

    def _compute(self, sku: str, memo: dict[str, TrialResult]) -> TrialResult:
        cached = self._cache.get(sku) or memo.get(sku)
        if cached is not None:
            return cached
        definition = self._skus[sku]
        inputs = [
            (item.quantity, self._compute(item.sku, memo)) for item in definition.bom
        ]
        if definition.tax_category is TaxCategory.EXEMPT:
            # 免税：输入已发生的销项税计入成本
            net_sum = definition.value_added + sum(
                (quantity * (child.net_price + child.output_tax)
                 for quantity, child in inputs),
                ZERO,
            )
        else:
            net_sum = definition.value_added + sum(
                (quantity * child.net_price for quantity, child in inputs),
                ZERO,
            )
        net_price = _round_cent(net_sum)
        if definition.tax_category is TaxCategory.TAXABLE:
            output_tax = _round_cent(net_price * definition.tax_rate)
        else:
            output_tax = ZERO.quantize(CENT)
        if definition.tax_category is TaxCategory.EXEMPT:
            deductible = ZERO.quantize(CENT)
        else:
            deductible = _round_cent(
                sum(
                    (quantity * child.output_tax for quantity, child in inputs),
                    ZERO,
                )
            )
        result = TrialResult(
            sku=sku,
            net_price=net_price,
            output_tax=output_tax,
            deductible_input_tax=deductible,
        )
        memo[sku] = result
        self._cache[sku] = result
        return result

    # ------------------------------------------------------------------ 校验

    def _normalize_definition(self, definition: SkuDefinition) -> SkuDefinition:
        normalized = SkuDefinition(
            sku=str(definition.sku),
            value_added=_to_decimal(definition.value_added, "加工增值额"),
            tax_category=TaxCategory(definition.tax_category),
            tax_rate=(
                None
                if definition.tax_rate is None
                else _to_decimal(definition.tax_rate, "税率")
            ),
            bom=_normalize_bom(definition.bom),
        )
        return self._check_definition(normalized)

    @staticmethod
    def _check_definition(definition: SkuDefinition) -> SkuDefinition:
        if definition.value_added != _round_cent(definition.value_added):
            raise InvalidDefinitionError(
                f"加工增值额须精确到分: {definition.sku}={definition.value_added}"
            )
        if definition.tax_category is TaxCategory.TAXABLE:
            if definition.tax_rate is None:
                raise InvalidDefinitionError(f"应税 SKU 缺少税率: {definition.sku}")
            if definition.tax_rate < 0:
                raise InvalidDefinitionError(f"税率不得为负: {definition.sku}")
        elif definition.tax_rate is not None:
            raise InvalidDefinitionError(
                f"非应税 SKU 不得设置税率: {definition.sku}"
            )
        for item in definition.bom:
            if item.quantity < 0:
                raise InvalidDefinitionError(
                    f"用量不得为负: {definition.sku} <- {item.sku}"
                )
        return definition

    @staticmethod
    def _validate_graph(skus: dict[str, SkuDefinition]) -> None:
        for definition in skus.values():
            for item in definition.bom:
                if item.sku not in skus:
                    raise UnknownSkuError(
                        f"配方引用了不存在的 SKU: {definition.sku} <- {item.sku}"
                    )
        # 拓扑排序检测闭环（含直接自引用）
        indegree = {sku: 0 for sku in skus}
        dependents: dict[str, list[str]] = {sku: [] for sku in skus}
        for definition in skus.values():
            for item in definition.bom:
                indegree[definition.sku] += 1
                dependents[item.sku].append(definition.sku)
        queue = [sku for sku, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            node = queue.pop()
            visited += 1
            for dependent in dependents[node]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
        if visited != len(skus):
            cyclic = sorted(sku for sku, degree in indegree.items() if degree > 0)
            raise CycleError(f"配方存在闭环，整批不生效: {cyclic}")
