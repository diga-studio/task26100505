# vat_cost — 多层物料增值税成本试算库

纯 Python、零依赖，数据仅保存在进程内，由上层直接调用。

## 能力

- `VatCostEngine.load(definitions)`：整批载入，替换全部数据；任一定义非法或配方成环则整体不生效。
- `VatCostEngine.adjust(adjustments)`：批量调整加工增值额 / 税类 / 税率 / 完整配方，按批次最终状态整体生效，与条目顺序无关；最终成环则整批拒绝，原数据不变。
- `VatCostEngine.query(sku)`：查询单个 SKU 的试算结果（未税试算价、销项税额、可抵扣进项税额）。

## 计算规则

- 应税 / 零税率 SKU 未税价 = 加工增值额 + Σ(用量 × 输入未税价)。
- 免税 SKU 未税价 = 加工增值额 + Σ(用量 × (输入未税价 + 输入销项税额))，即进项税计入成本。
- 应税 SKU 销项税 = 已舍入未税价 × 税率；其余为零。
- 应税 / 零税率 SKU 可抵扣进项税 = Σ(用量 × 输入销项税额)；免税为零。
- 三项均按十进制 `ROUND_HALF_UP` 舍入到分；未税价先汇总再舍入，销项税用已舍入价格计算；上层始终使用输入已舍入的价格与税额。

## 示例

```python
from decimal import Decimal
from vat_cost import VatCostEngine, SkuDefinition, BomItem, TaxCategory, Adjustment

engine = VatCostEngine()
engine.load([
    SkuDefinition("RAW", Decimal("10.00"), TaxCategory.TAXABLE, Decimal("0.13")),
    SkuDefinition("MID", Decimal("5.00"), TaxCategory.TAXABLE, Decimal("0.13"),
                  (BomItem("RAW", Decimal("2")),)),
    SkuDefinition("FIN", Decimal("1.00"), TaxCategory.EXEMPT, None,
                  (BomItem("MID", Decimal("1")),)),
])
engine.adjust([Adjustment("RAW", value_added=Decimal("12.00"))])
print(engine.query("FIN"))
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```
