# 逐步读取与核验

所有调用使用 `runtime {"op":"files","args":{...}}`，没有新工具或登录步骤。

- `action=list`：可选 session_id、offset（默认 0）、limit（默认 50，上限 200）。返回 files、total、next_offset。发现已有文件会保留其 ID，不复制或新建版本。
- `action=read`：必需 file_id；可传上次收到的 content_revision。文件改变时返回 file_changed，先刷新列表和版本再核验，不能拼接不同版本的数据。返回 file 为已登记引用，preview 为用户看到的同源语义，range 为本次读取范围。list/read 的 delivery 说明直接交付链接和默认文件展示行为，不是浏览器渲染回执。
- 表格：sheet_index 默认 0；offset/limit 按该工作表预览行计，默认 0/20（上限 100）；column_offset/column_limit 按列计，默认 0/12（上限 40）。表格有行空隙时以 row_numbers 为真实 Excel 行号，列号是 column_offset 加 cells 内索引。range.sheets 给出可读工作表；range.next_offset 和 next_column_offset 给出剩余页。大页会自动缩小，按实际返回范围继续读取，不能假设请求大小都返回了。
- 文本、Markdown、HTML：offset/limit 按字符计，默认 0/8000，limit 上限 12000。Word 按根块分页；过大的单块明确 requires_cli=true，改用有界脚本读取原文件，不把空块页视为无内容。图片只返回原文件预览引用，没有 OCR 声明。

## 表格含义

每页 `preview.sheets[0].rows` 是显示文本，`cells` 与之逐格对齐。每格 address 是原工作表内的绝对坐标（如 B7、AA42），已考虑分页列偏移与源行号，引用时直接使用。它由同一预览数据补充，不改变原文件或用户界面的预览结构。`raw` 是存储值，`display` 是受支持格式的显示值；日期规范为 ISO，不是完整 Excel 排版。金额只采用文件明确给出的币种，不自行假设。

- kind=formula、formula_status=missing：没有已保存结果。已知 formula 时显示 `=公式`；共享公式可能没有公式正文。必须说“尚无计算结果，无法仅凭缓存判断”，不能称为空金额或按 0 核对。
- formula_status=saved：文件有缓存；raw="" 且 display="" 才可能是真正空字符串。raw="0"、display="" 可能是格式隐藏零值，仍是数字 0。
- `formula_results=not_recalculated`：这次读取没有重算公式。`calculation_warning=true` 表示工作簿要求重算或采用手动计算，保存结果可能旧；没有该标记也不证明缓存新鲜。
- format_status=unsupported：保留原始值；不要按表面数字推断百分比、币种或日期。
- preview.truncated 或 sheet.truncated 表示源预览本身不完整；range 分页只遍历已提供部分。当前最多 6 表、每表 200 行/40 列，单字段 2000 字符。超出范围应通过 CLI 检查原文件并明确边界，不能声称读完全文。

“检查报价算得对不对”需要区分已核对的输入、独立计算结果、文件缓存和无法核验的公式。可用短 Python/Decimal 脚本独立核对需要的金额，写清假设；预览不提供 Excel 公式计算引擎。核对结果不应覆盖用户原件。只有用户要求修改或重算并具备实际能力时才另存输出、复读验收。
