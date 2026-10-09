# 新建表格与公式结果

本机已提供 XlsxWriter 和 openpyxl。新建 `.xlsx` 优先 XlsxWriter；它只写新工作簿，不能用来打开并覆盖已有文件。编辑已有表格仍用 openpyxl，另存文件并保护原件。

两者都不是公式计算引擎。XlsxWriter 未指定结果时默认缓存 0，不能让这个占位值冒充计算结果。新表输入及公式逻辑已知时，用 Python 独立算出结果，并将它传给 `write_formula` 的 `value` 参数。例如金额逐项为 12、35、20：

```python
from decimal import Decimal
import xlsxwriter

amounts = [Decimal("12"), Decimal("35"), Decimal("20")]
with xlsxwriter.Workbook("日常开支.xlsx") as book:
    sheet = book.add_worksheet("开支")
    money = book.add_format({"num_format": "0.00"})
    sheet.write_row("A1", ["项目", "金额"])
    for row, amount in enumerate(amounts, 1):
        sheet.write_number(row, 1, float(amount), money)
    sheet.write("A5", "合计")
    sheet.write_formula("B5", "=SUM(B2:B4)", money, float(sum(amounts)))
```

复读时分别用 `openpyxl.load_workbook(path, data_only=False/True)` 核对公式与缓存，再用 `files read` 核对用户预览。本例必须同时保留 `=SUM(B2:B4)` 与结果 67，不能仅把 B5 写成 67。预览的 `not_recalculated` 不否定这次独立核对，不为清除提示删除公式。不要复读后再用 openpyxl 保存这个新文件，它不会重算公式，也可能丢失缓存。结果只代表已核对的当前输入；没有核实的公式不能编造缓存，也不要承诺已计算任意 Excel 公式。确实无法核验的部分，才向用户简要说明还需计算。

依据：[XlsxWriter 公式结果说明](https://xlsxwriter.readthedocs.io/working_with_formulas.html#formula-results)及 [write_formula 接口](https://xlsxwriter.readthedocs.io/worksheet.html#write_formula)。
