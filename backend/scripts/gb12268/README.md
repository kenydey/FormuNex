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
