"""vat_cost 自动化测试。"""
from __future__ import annotations

import unittest
from decimal import Decimal, ROUND_HALF_UP

from vat_cost import (
    Adjustment,
    BomItem,
    CycleError,
    InvalidDefinitionError,
    SkuDefinition,
    TaxCategory,
    TrialResult,
    UnknownSkuError,
    VatCostEngine,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def reference_trial(definitions, sku, _memo=None):
    """独立的从头试算：按规格直接递归计算，不经过引擎。"""
    if _memo is None:
        _memo = {}
    if sku in _memo:
        return _memo[sku]
    definition = {d.sku: d for d in definitions}[sku]
    children = [
        (item.quantity, reference_trial(definitions, item.sku, _memo))
        for item in definition.bom
    ]
    if definition.tax_category is TaxCategory.EXEMPT:
        net_sum = definition.value_added + sum(
            (qty * (child.net_price + child.output_tax) for qty, child in children),
            Decimal(0),
        )
    else:
        net_sum = definition.value_added + sum(
            (qty * child.net_price for qty, child in children), Decimal(0)
        )
    net_price = cent(net_sum)
    if definition.tax_category is TaxCategory.TAXABLE:
        output_tax = cent(net_price * definition.tax_rate)
        deductible = cent(
            sum((qty * child.output_tax for qty, child in children), Decimal(0))
        )
    elif definition.tax_category is TaxCategory.ZERO_RATED:
        output_tax = ZERO
        deductible = cent(
            sum((qty * child.output_tax for qty, child in children), Decimal(0))
        )
    else:
        output_tax = ZERO
        deductible = ZERO
    result = TrialResult(sku, net_price, output_tax, deductible)
    _memo[sku] = result
    return result


def shared_definitions():
    """共享子件 S 经多条路径进入成品 F，混合免税 E 与零税率 Z。

    S: 应税 10%，增值 10.25 -> 销项 1.025 恰好半分，舍入为 1.03
    Z: 零税率，3 份 S
    E: 免税，1 份 S + 2 份 Z（S 的销项税计入成本）
    F: 应税 10%，2.5 份 S + 1 份 E + 1 份 Z
       未税价 140.155 恰好半分 -> 140.16；进项 2.575 恰好半分 -> 2.58
    """
    return [
        SkuDefinition("S", Decimal("10.25"), TaxCategory.TAXABLE, Decimal("0.10")),
        SkuDefinition(
            "Z", Decimal("2.00"), TaxCategory.ZERO_RATED, None,
            (BomItem("S", Decimal("3")),),
        ),
        SkuDefinition(
            "E", Decimal("1.00"), TaxCategory.EXEMPT, None,
            (BomItem("S", Decimal("1")), BomItem("Z", Decimal("2"))),
        ),
        SkuDefinition(
            "F", Decimal("4.00"), TaxCategory.TAXABLE, Decimal("0.10"),
            (
                BomItem("S", Decimal("2.5")),
                BomItem("E", Decimal("1")),
                BomItem("Z", Decimal("1")),
            ),
        ),
    ]


class SharedBomTest(unittest.TestCase):
    def setUp(self):
        self.definitions = shared_definitions()
        self.engine = VatCostEngine()
        self.engine.load(self.definitions)

    def test_shared_bom_matches_reference(self):
        for definition in self.definitions:
            with self.subTest(sku=definition.sku):
                self.assertEqual(
                    self.engine.query(definition.sku),
                    reference_trial(self.definitions, definition.sku),
                )

    def test_exact_half_cent_rounding(self):
        shared = self.engine.query("S")
        self.assertEqual(shared.output_tax, Decimal("1.03"))  # 1.025 半分向上
        finished = self.engine.query("F")
        self.assertEqual(finished.net_price, Decimal("140.16"))   # 140.155 半分向上
        self.assertEqual(finished.output_tax, Decimal("14.02"))   # 140.16 * 0.10
        self.assertEqual(finished.deductible_input_tax, Decimal("2.58"))  # 2.575

    def test_exempt_counts_input_tax_as_cost(self):
        exempt = self.engine.query("E")
        # 1.00 + (10.25 + 1.03) + 2 * (32.75 + 0.00)
        self.assertEqual(exempt.net_price, Decimal("77.78"))
        self.assertEqual(exempt.output_tax, Decimal("0.00"))
        self.assertEqual(exempt.deductible_input_tax, Decimal("0.00"))

    def test_zero_rated_is_not_exempt(self):
        zero = self.engine.query("Z")
        self.assertEqual(zero.net_price, Decimal("32.75"))  # 不计入 S 的销项税
        self.assertEqual(zero.output_tax, Decimal("0.00"))
        self.assertEqual(zero.deductible_input_tax, Decimal("3.09"))  # 进项可抵扣

    def test_adjust_consistent_with_fresh_load(self):
        # 调整共享子件增值额后，引擎结果与从头试算一致
        self.engine.adjust([Adjustment("S", value_added=Decimal("20.00"))])
        new_definitions = [
            SkuDefinition("S", Decimal("20.00"), TaxCategory.TAXABLE, Decimal("0.10")),
            *self.definitions[1:],
        ]
        for definition in new_definitions:
            with self.subTest(sku=definition.sku):
                self.assertEqual(
                    self.engine.query(definition.sku),
                    reference_trial(new_definitions, definition.sku),
                )


class BatchAdjustTest(unittest.TestCase):
    @staticmethod
    def _definitions():
        return [
            SkuDefinition("A", Decimal("10.00"), TaxCategory.TAXABLE, Decimal("0.13")),
            SkuDefinition(
                "B", Decimal("1.00"), TaxCategory.TAXABLE, Decimal("0.13"),
                (BomItem("A", Decimal("1")),),
            ),
            SkuDefinition(
                "C", Decimal("1.00"), TaxCategory.TAXABLE, Decimal("0.13"),
                (BomItem("B", Decimal("1")),),
            ),
        ]

    def setUp(self):
        self.engine = VatCostEngine()
        self.engine.load(self._definitions())

    def test_temporary_cycle_in_batch_is_accepted(self):
        # 同批：C 改为引用 A（断开 C<-B），B 改为引用 C（若单独应用则成环）
        batch = [
            Adjustment("C", bom=(BomItem("A", Decimal("2")),)),
            Adjustment("B", bom=(BomItem("C", Decimal("1")),)),
        ]
        self.engine.adjust(batch)
        self.assertEqual(self.engine.query("C").net_price, Decimal("21.00"))
        self.assertEqual(self.engine.query("B").net_price, Decimal("22.00"))

    def test_entry_order_does_not_matter(self):
        forward = VatCostEngine()
        forward.load(self._definitions())
        reverse = VatCostEngine()
        reverse.load(self._definitions())
        batch = [
            Adjustment("C", bom=(BomItem("A", Decimal("2")),)),
            Adjustment("B", bom=(BomItem("C", Decimal("1")),)),
        ]
        forward.adjust(batch)
        reverse.adjust(list(reversed(batch)))
        for sku in ("A", "B", "C"):
            self.assertEqual(forward.query(sku), reverse.query(sku))

    def test_final_cycle_rejected_and_state_untouched(self):
        before = {sku: self.engine.query(sku) for sku in ("A", "B", "C")}
        with self.assertRaises(CycleError):
            self.engine.adjust(
                [
                    Adjustment("B", bom=(BomItem("C", Decimal("1")),)),
                    Adjustment("C", bom=(BomItem("B", Decimal("1")),)),
                ]
            )
        # 原数据与试算结果不变
        for sku, result in before.items():
            self.assertEqual(self.engine.query(sku), result)
        # 后续合法调整不受污染
        self.engine.adjust([Adjustment("B", value_added=Decimal("2.00"))])
        self.assertEqual(self.engine.query("B").net_price, Decimal("12.00"))
        self.assertEqual(self.engine.query("C").net_price, Decimal("13.00"))

    def test_direct_self_reference_rejected(self):
        with self.assertRaises(CycleError):
            self.engine.adjust(
                [Adjustment("B", bom=(BomItem("B", Decimal("1")),))]
            )
        self.assertEqual(self.engine.query("B").net_price, Decimal("11.00"))

    def test_load_with_cycle_rejected(self):
        engine = VatCostEngine()
        with self.assertRaises(CycleError):
            engine.load(
                [
                    SkuDefinition("X", Decimal("1.00"), TaxCategory.EXEMPT, None,
                                  (BomItem("Y", Decimal("1")),)),
                    SkuDefinition("Y", Decimal("1.00"), TaxCategory.EXEMPT, None,
                                  (BomItem("X", Decimal("1")),)),
                ]
            )


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.engine = VatCostEngine()
        self.engine.load(
            [SkuDefinition("A", Decimal("10.00"), TaxCategory.TAXABLE, Decimal("0.13"))]
        )

    def test_taxable_requires_rate(self):
        with self.assertRaises(InvalidDefinitionError):
            VatCostEngine().load(
                [SkuDefinition("X", Decimal("1.00"), TaxCategory.TAXABLE, None)]
            )

    def test_non_taxable_must_not_have_rate(self):
        with self.assertRaises(InvalidDefinitionError):
            VatCostEngine().load(
                [SkuDefinition("X", Decimal("1.00"), TaxCategory.EXEMPT, Decimal("0.1"))]
            )

    def test_value_added_must_be_cents(self):
        with self.assertRaises(InvalidDefinitionError):
            VatCostEngine().load(
                [SkuDefinition("X", Decimal("1.005"), TaxCategory.EXEMPT)]
            )

    def test_unknown_bom_reference(self):
        with self.assertRaises(UnknownSkuError):
            VatCostEngine().load(
                [SkuDefinition("X", Decimal("1.00"), TaxCategory.EXEMPT, None,
                               (BomItem("MISSING", Decimal("1")),))]
            )

    def test_unknown_adjust_target(self):
        with self.assertRaises(UnknownSkuError):
            self.engine.adjust([Adjustment("MISSING", value_added=Decimal("1.00"))])

    def test_query_unknown_sku(self):
        with self.assertRaises(UnknownSkuError):
            self.engine.query("MISSING")

    def test_duplicate_definition_rejected(self):
        with self.assertRaises(InvalidDefinitionError):
            VatCostEngine().load(
                [
                    SkuDefinition("X", Decimal("1.00"), TaxCategory.EXEMPT),
                    SkuDefinition("X", Decimal("2.00"), TaxCategory.EXEMPT),
                ]
            )

    def test_failed_adjust_leaves_state_untouched(self):
        with self.assertRaises(InvalidDefinitionError):
            self.engine.adjust(
                [Adjustment("A", tax_category=TaxCategory.EXEMPT)]  # 仍带税率 -> 非法
            )
        self.assertEqual(self.engine.query("A").net_price, Decimal("10.00"))
        self.assertEqual(self.engine.query("A").output_tax, Decimal("1.30"))


if __name__ == "__main__":
    unittest.main()
