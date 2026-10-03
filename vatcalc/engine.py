"""多层物料增值税成本试算引擎（进程内数据）。"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import AdjustEntry, SkuDefinition, TaxClass, TrialResult

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")


def _round_cent(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class VatTrialEngine:
    """维护 SKU 定义，提供整批载入、批量调整、单 SKU 试算查询。"""

    def __init__(self) -> None:
        self._skus: dict[str, SkuDefinition] = {}

    # ---- 写入 ----

    def load_batch(self, definitions: dict[str, SkuDefinition]) -> None:
        """整批载入：替换全部数据。最终配方有环则整批不生效。"""
        normalized = {sku: self._normalize(defn) for sku, defn in definitions.items()}
        self._validate(normalized)
        self._skus = normalized

    def adjust_batch(self, entries: list[AdjustEntry]) -> None:
        """批量调整：按批次最终状态整体生效，与条目顺序无关。

        先在副本上应用全部条目并校验，通过后才替换原数据；
        任何一条非法或最终配方成环，整批不生效。
        """
        new_skus = dict(self._skus)
        for entry in entries:
            if entry.sku not in new_skus:
                raise ValueError(f"未知 SKU: {entry.sku}")
            current = new_skus[entry.sku]
            tax_class = entry.tax_class if entry.tax_class is not None else current.tax_class
            if entry.tax_rate is not None:
                tax_rate = entry.tax_rate
            elif entry.tax_class is not None and tax_class is not TaxClass.TAXABLE:
                tax_rate = None
            else:
                tax_rate = current.tax_rate
            new_skus[entry.sku] = SkuDefinition(
                value_added=entry.value_added if entry.value_added is not None else current.value_added,
                tax_class=tax_class,
                tax_rate=tax_rate,
                recipe=dict(entry.recipe) if entry.recipe is not None else dict(current.recipe),
            )
        normalized = {sku: self._normalize(defn) for sku, defn in new_skus.items()}
        self._validate(normalized)
        self._skus = normalized

    # ---- 查询 ----

    def query(self, sku: str) -> TrialResult:
        """查询单个 SKU 的试算结果（未税价 / 销项税 / 可抵扣进项税）。"""
        if sku not in self._skus:
            raise KeyError(f"未知 SKU: {sku}")
        memo: dict[str, TrialResult] = {}
        return self._compute(sku, memo, ())

    # ---- 内部 ----

    def _compute(
        self, sku: str, memo: dict[str, TrialResult], path: tuple[str, ...]
    ) -> TrialResult:
        if sku in memo:
            return memo[sku]
        if sku in path:
            raise ValueError(f"配方存在闭环: {' -> '.join(path + (sku,))}")
        defn = self._skus[sku]
        inputs = [
            (qty, self._compute(inp, memo, path + (sku,)))
            for inp, qty in defn.recipe.items()
        ]
        if defn.tax_class is TaxClass.EXEMPT:
            # 免税：输入已发生的销项税计入成本
            net_raw = defn.value_added + sum(
                (qty * (inp.net_price + inp.output_tax) for qty, inp in inputs), _ZERO
            )
        else:
            net_raw = defn.value_added + sum((qty * inp.net_price for qty, inp in inputs), _ZERO)
        net = _round_cent(net_raw)
        if defn.tax_class is TaxClass.TAXABLE:
            output_tax = _round_cent(net * defn.tax_rate)
        else:
            output_tax = _ZERO
        if defn.tax_class is TaxClass.EXEMPT:
            deductible = _ZERO
        else:
            deductible = _round_cent(sum((qty * inp.output_tax for qty, inp in inputs), _ZERO))
        result = TrialResult(net_price=net, output_tax=output_tax, deductible_input_tax=deductible)
        memo[sku] = result
        return result

    @staticmethod
    def _normalize(defn: SkuDefinition) -> SkuDefinition:
        tax_rate = defn.tax_rate
        if defn.tax_class is TaxClass.TAXABLE:
            if tax_rate is None:
                raise ValueError("应税 SKU 必须设置税率")
            if tax_rate < 0:
                raise ValueError("税率不得为负")
        elif tax_rate is not None:
            raise ValueError("零税率/免税 SKU 不得设置税率")
        recipe = {inp: Decimal(qty) for inp, qty in defn.recipe.items()}
        for inp, qty in recipe.items():
            if qty <= 0:
                raise ValueError(f"配方用量必须为正: {inp}")
        return SkuDefinition(
            value_added=_round_cent(defn.value_added),
            tax_class=defn.tax_class,
            tax_rate=tax_rate,
            recipe=recipe,
        )

    @staticmethod
    def _validate(skus: dict[str, SkuDefinition]) -> None:
        for sku, defn in skus.items():
            for inp in defn.recipe:
                if inp not in skus:
                    raise ValueError(f"SKU {sku} 的配方引用了未知 SKU: {inp}")
        # 整体有环检测：任何 SKU 不得直接或间接包含自身
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {sku: WHITE for sku in skus}
        for root in skus:
            if color[root] != WHITE:
                continue
            stack = [(root, iter(skus[root].recipe))]
            color[root] = GRAY
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if color[nxt] == GRAY:
                        raise ValueError(f"配方存在闭环，涉及 SKU: {nxt}")
                    if color[nxt] == WHITE:
                        color[nxt] = GRAY
                        stack.append((nxt, iter(skus[nxt].recipe)))
                        advanced = True
                        break
                if not advanced:
                    color[node] = BLACK
                    stack.pop()
