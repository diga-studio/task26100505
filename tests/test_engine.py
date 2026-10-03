import unittest
from decimal import ROUND_HALF_UP, Decimal

from vatcalc import AdjustEntry, SkuDefinition, TaxClass, TrialResult, VatTrialEngine

CENT = Decimal("0.01")


def rc(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def reference_compute(defs: dict[str, SkuDefinition], sku: str, memo: dict) -> TrialResult:
    """独立的从头试算实现，用于交叉验证引擎结果。"""
    if sku in memo:
        return memo[sku]
    d = defs[sku]
    inputs = [(q, reference_compute(defs, i, memo)) for i, q in d.recipe.items()]
    if d.tax_class is TaxClass.EXEMPT:
        net = rc(d.value_added + sum((q * (r.net_price + r.output_tax) for q, r in inputs), Decimal(0)))
        out, ded = Decimal("0.00"), Decimal("0.00")
    else:
        net = rc(d.value_added + sum((q * r.net_price for q, r in inputs), Decimal(0)))
        out = rc(net * d.tax_rate) if d.tax_class is TaxClass.TAXABLE else Decimal("0.00")
        ded = rc(sum((q * r.output_tax for q, r in inputs), Decimal(0)))
    memo[sku] = TrialResult(net, out, ded)
    return memo[sku]


def build_shared_defs() -> dict[str, SkuDefinition]:
    return {
        # 原料
        "steel": SkuDefinition(Decimal("10.01"), TaxClass.TAXABLE, Decimal("0.13")),
        "plastic": SkuDefinition(Decimal("4.00"), TaxClass.ZERO_RATED),
        "glue": SkuDefinition(Decimal("2.50"), TaxClass.EXEMPT),
        # 共享中间件：0.5*10.01=5.005 恰好半分，四舍五入到 8.51
        "part-x": SkuDefinition(
            Decimal("1.00"), TaxClass.TAXABLE, Decimal("0.13"),
            {"steel": Decimal("0.5"), "glue": Decimal("1")},
        ),
        # 免税中间件：输入销项税计入成本
        "part-y": SkuDefinition(
            Decimal("0.50"), TaxClass.EXEMPT, None,
            {"part-x": Decimal("2"), "plastic": Decimal("1")},
        ),
        # 成品：part-x 经直接引用与 part-y 两条路径进入
        "product": SkuDefinition(
            Decimal("3.00"), TaxClass.TAXABLE, Decimal("0.13"),
            {"part-x": Decimal("1"), "part-y": Decimal("1")},
        ),
    }


class SharedSubcomponentTest(unittest.TestCase):
    def setUp(self):
        self.defs = build_shared_defs()
        self.engine = VatTrialEngine()
        self.engine.load_batch(self.defs)

    def test_shared_part_mixed_tax_and_half_cent_rounding(self):
        part_x = self.engine.query("part-x")
        self.assertEqual(part_x.net_price, Decimal("8.51"))   # 8.505 半分进位
        self.assertEqual(part_x.output_tax, Decimal("1.11"))  # 8.51*0.13=1.1063
        self.assertEqual(part_x.deductible_input_tax, Decimal("0.65"))  # 0.5*1.30

        part_y = self.engine.query("part-y")
        self.assertEqual(part_y.net_price, Decimal("23.74"))  # 0.5+2*(8.51+1.11)+4.00
        self.assertEqual(part_y.output_tax, Decimal("0.00"))
        self.assertEqual(part_y.deductible_input_tax, Decimal("0.00"))

        product = self.engine.query("product")
        self.assertEqual(product.net_price, Decimal("35.25"))  # 3+8.51+23.74
        self.assertEqual(product.output_tax, Decimal("4.58"))  # 35.25*0.13=4.5825
        self.assertEqual(product.deductible_input_tax, Decimal("1.11"))

    def test_results_match_independent_recompute(self):
        memo: dict = {}
        for sku in self.defs:
            self.assertEqual(self.engine.query(sku), reference_compute(self.defs, sku, memo))

    def test_exempt_is_not_zero_rated(self):
        # 零税率 plastic 的成本不含任何销项税；免税 part-y 的成本含 part-x 的销项税
        plastic = self.engine.query("plastic")
        self.assertEqual(plastic.net_price, Decimal("4.00"))
        part_y = self.engine.query("part-y")
        self.assertEqual(part_y.net_price, Decimal("23.74"))  # 含 2*1.11 的进项税成本


class BatchAdjustTest(unittest.TestCase):
    def setUp(self):
        self.engine = VatTrialEngine()
        self.engine.load_batch({
            "A": SkuDefinition(Decimal("1.00"), TaxClass.TAXABLE, Decimal("0.13"),
                               {"B": Decimal("1")}),
            "B": SkuDefinition(Decimal("2.00"), TaxClass.TAXABLE, Decimal("0.13"),
                               {"C": Decimal("1")}),
            "C": SkuDefinition(Decimal("5.00"), TaxClass.TAXABLE, Decimal("0.13")),
        })

    def test_temporary_cycle_final_acyclic_succeeds_any_order(self):
        # 逐条应用时 B.recipe={A} 会暂时成环 A->B->A，但 A.recipe={C} 后闭环消失
        entries = [
            AdjustEntry("B", recipe={"A": Decimal("1")}),
            AdjustEntry("A", recipe={"C": Decimal("2")}),
        ]
        results = []
        for ordered in (entries, list(reversed(entries))):
            engine = VatTrialEngine()
            engine.load_batch({
                "A": SkuDefinition(Decimal("1.00"), TaxClass.TAXABLE, Decimal("0.13"),
                                   {"B": Decimal("1")}),
                "B": SkuDefinition(Decimal("2.00"), TaxClass.TAXABLE, Decimal("0.13"),
                                   {"C": Decimal("1")}),
                "C": SkuDefinition(Decimal("5.00"), TaxClass.TAXABLE, Decimal("0.13")),
            })
            engine.adjust_batch(ordered)
            results.append({s: engine.query(s) for s in ("A", "B", "C")})
        self.assertEqual(results[0], results[1])  # 与条目顺序无关
        # 最终：A=1+2*5=11, B=2+11=13
        self.assertEqual(results[0]["A"].net_price, Decimal("11.00"))
        self.assertEqual(results[0]["B"].net_price, Decimal("13.00"))

    def test_final_cycle_rejected_and_data_unchanged(self):
        before = {s: self.engine.query(s) for s in ("A", "B", "C")}
        with self.assertRaises(ValueError):
            self.engine.adjust_batch([AdjustEntry("C", recipe={"A": Decimal("1")})])
        after = {s: self.engine.query(s) for s in ("A", "B", "C")}
        self.assertEqual(before, after)
        # 后续合法调整不受污染
        self.engine.adjust_batch([AdjustEntry("C", value_added=Decimal("6.00"))])
        self.assertEqual(self.engine.query("C").net_price, Decimal("6.00"))
        self.assertEqual(self.engine.query("B").net_price, Decimal("8.00"))

    def test_load_batch_with_cycle_rejected(self):
        engine = VatTrialEngine()
        with self.assertRaises(ValueError):
            engine.load_batch({
                "X": SkuDefinition(Decimal("1.00"), TaxClass.TAXABLE, Decimal("0.13"),
                                   {"Y": Decimal("1")}),
                "Y": SkuDefinition(Decimal("1.00"), TaxClass.TAXABLE, Decimal("0.13"),
                                   {"X": Decimal("1")}),
            })
        with self.assertRaises(KeyError):
            engine.query("X")

    def test_adjust_unknown_sku_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.adjust_batch([AdjustEntry("ZZZ", value_added=Decimal("1.00"))])
        self.assertEqual(self.engine.query("A").net_price, Decimal("8.00"))

    def test_tax_class_change_clears_rate(self):
        self.engine.adjust_batch([AdjustEntry("C", tax_class=TaxClass.EXEMPT)])
        c = self.engine.query("C")
        self.assertEqual(c.output_tax, Decimal("0.00"))
        self.assertEqual(c.deductible_input_tax, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
