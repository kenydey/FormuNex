# GB 12268-2025 品名表解析

`app/resources/regulatory/gb12268_2025.json` 由这里的脚本从 GB 12268-2025 官方 PDF
（国家标准全文公开系统下载，sha256 `809d35ae…16e2fe`）生成。PDF 不随仓库分发。

PDF 正文使用方正私有编码字体（Founder PKU2 / FzBookMaker 动态子集），文本层无法直接读取，因此：

1. `extract.py PDF glyphs.json` — 读取表 1 各页的表格线与每个字形（位置、所在栏、轮廓哈希）。
   同一个字在所有页面的轮廓相同，哈希即字的身份（全表 1,181 种字形）。
2. `ocrlines.py PDF glyphs.json lines.json` — 按单元格把字形分行，逐行渲染做 OCR（RapidOCR）。
3. `decode.py lines.json glyphs.json overrides.json out` — 长度一致的 OCR 行按字形投票定字；
   `overrides.json` 是人工逐一看图核对后改定的字形（低频、投票分歧、元素名等化学专用字）。
   表格按表格线组装：每条竖线只切分它实际穿过的栏，合并单元格展开到各子行。
4. `finalize.py out_entries.json SHA gb12268_2025.json [2017 UN 品名表.xlsx]` — 规范化（中文全角标点、
   英文半角、包装类别 I/II/III、“—”为空）。可选的 2017 年联合国品名表只在英文名称字母完全一致时
   提供词间空格，不提供任何内容。

依赖（独立虚拟环境即可）：`pymupdf fonttools rapidocr_onnxruntime openpyxl numpy pillow`。

校验结果：联合国编号 2,347 个、严格递增，与 2017 年联合国品名表相比仅缺 UN 1169（2025 版删除）、
多出 2025 版新增条目；类别与包装类别值域全部合法；92% 行的英文名称字母与 2017 年表逐字一致，
其余为版本修订、℃ / mL 写法及原文印刷错误（原样保留，见资源文件 `meta.printed_typos_kept`）。

## 名称对应多个物质的条目（人工选定关联）

导入时，GB 12268 中文名称在物质库里对应多个物质的条目（如“甲醇”同时匹配 67-56-1 和一个重复登记号）
无法自动关联，进入复核队列（`transport_unlinked`）。处理流程：

1. `build_link_sheet.py --out gb12268-关联核对表.xlsx` — 生成核对表：每条列出全部候选物质（CAS、英文名、
   分子式、《危险化学品目录》序号、是否列入 IECSC）以及 `link_suggestions.json` 中的建议与理由（助手整理的
   草稿，仅供参考）。
2. EHS / 法规专员填写黄色单元格：“选定”可选某一个 CAS、“全部候选”（UN 条目同时适用于全部候选，如不同水合物或
   复数条目）或“不关联”，也可填写多个 CAS（用“；”分隔）。
3. `apply_link_sheet.py 填好的表.xlsx` — 写入 `app/resources/regulatory/gb12268_links.json`（校验 CAS 校验位，
   与已有决定合并），提交后重新导入法规库即生效：选定的物质各关联一行，相应复核项不再出现；选定的 CAS 在物质库
   中不存在时记为 `transport_link_missing` 复核项，不静默丢弃。
