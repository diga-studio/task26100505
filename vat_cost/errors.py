"""库对外抛出的异常。"""


class VatCostError(Exception):
    """本库所有异常的基类。"""


class UnknownSkuError(VatCostError):
    """引用了不存在的 SKU。"""


class CycleError(VatCostError):
    """批次最终状态的配方存在直接或间接自引用闭环，整批不生效。"""


class InvalidDefinitionError(VatCostError):
    """定义或调整本身非法（如应税 SKU 缺税率、非应税 SKU 带税率、负数等）。"""
