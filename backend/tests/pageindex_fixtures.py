"""Synthetic long patents for PageIndex tests.

Each patent spreads one answer over distant pages, the way real anticorrosion
patents do: the precursor is defined in 发明内容, the recipe table sits with the
examples, and the salt-spray results table comes ten pages later, tied to the
recipes only by example numbers and "见表 N". The three variants differ in table
numbering, language and where the cross reference lives, so a navigator cannot
pass by pattern-matching one layout.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyntheticPatent:
    source_id: str
    title: str
    pages: dict[int, str]
    recipe_label: str
    performance_label: str
    definition_term: str
    queries: dict[str, set[str]]  # query -> labels / section titles a correct walk must read

    def page_marked_text(self) -> str:
        return "\n".join(f"<!-- page:{p} -->\n{self.pages[p]}" for p in sorted(self.pages))

    def chunks(self) -> list[dict]:
        """One chunk per page, like the page-wise chunker produces for plain PDFs."""
        out = []
        for i, p in enumerate(sorted(self.pages)):
            text = self.pages[p]
            block = "table" if text.lstrip().startswith(("表", "Table")) else "text"
            out.append({"id": f"{self.source_id}-c{i:02d}", "ord": i, "text": text, "heading_path": "",
                        "page_no": p, "page_end": p, "block_type": block})
        return out


def _filler(topic: str, n: int = 3) -> str:
    return "\n".join(f"{topic}的一般性说明第{i}段，涉及涂装工艺、施工条件与储存稳定性等常规内容。" for i in range(1, n + 1))


def patent_cn_basic() -> SyntheticPatent:
    pages = {
        1: "一种无铬钝化防腐涂料及其制备方法\n摘要\n本发明公开了一种以硅烷前体为核心的无铬防腐涂料。",
        2: "权利要求书\n1. 一种防腐涂料，其特征在于包含前体A、环氧树脂和磷酸锌。\n2. 根据权利要求1所述的涂料，前体A的含量为5-15%。",
        3: "技术领域\n" + _filler("金属表面处理"),
        4: "背景技术\n" + _filler("六价铬钝化"),
        5: "发明内容\n本发明所述前体A定义为：3-氨丙基三乙氧基硅烷与双酚A环氧树脂按摩尔比2:1预反应得到的硅烷化环氧前体，环氧当量为320-360 g/eq。",
        6: _filler("前体A的制备", 4),
        7: "附图说明\n" + _filler("附图"),
        8: "具体实施方式\n" + _filler("实施方式"),
        9: "实施例1\n按表1所列配方，将前体A、环氧树脂E51和磷酸锌混合研磨后喷涂于冷轧钢板，180℃固化20 min。",
        10: "表1 实施例1-3的配方组成（质量份）\n前体A 10 12 8\n环氧树脂E51 60 58 62\n磷酸锌 15 15 15\n二甲苯 15 15 15",
        11: "实施例2\n按表1配方，提高前体A用量至12份，其余步骤同实施例1，喷涂膜厚控制在60-80 μm，固化后室温放置24 h再测试。",
        12: "实施例3\n按表1配方，前体A用量降低至8份，其余步骤同实施例1，喷涂膜厚控制在60-80 μm，固化后室温放置24 h再测试。",
        13: "对比例1\n不添加前体A，以等量环氧树脂E51补足，其余步骤同实施例1，用于考察前体A对耐蚀性的贡献。",
        14: _filler("固化工艺"),
        15: _filler("膜厚控制"),
        16: _filler("涂装设备"),
        17: _filler("储存"),
        18: "表2 实施例1-3及对比例1的性能测试结果\n中性盐雾(h) 1000 1200 800 240\n附着力(级) 0 0 1 2\n铅笔硬度 2H 2H H HB",
        19: "由表2可见，实施例1的中性盐雾达到1000 h，实施例2达到1200 h，均明显优于不含前体A的对比例1（240 h）。",
        20: _filler("环保性"),
        21: _filler("成本"),
        22: _filler("应用领域"),
        23: _filler("变形实施"),
        24: _filler("结语"),
    }
    return SyntheticPatent(
        "pat-cn-basic", "一种无铬钝化防腐涂料及其制备方法", pages, "table:1", "table:2", "前体A",
        {
            "实施例1的配方组成及其中性盐雾测试结果": {"table:1", "table:2"},
            "前体A的定义是什么，在哪些实施例的配方中使用": {"发明内容", "table:1"},
        },
    )


def patent_cn_renumbered() -> SyntheticPatent:
    """Tables 3 / 5, Chinese-numeral examples, and the results table cites the examples, not the recipe table."""
    pages = {p: _filler(f"第{p}页背景") for p in range(1, 27)}
    pages.update({
        2: "权利要求书\n1. 一种水性硅烷前处理剂，包含改性前体B与氟锆酸。",
        5: "发明内容\n前体B定义为：N-(2-氨乙基)-3-氨丙基三甲氧基硅烷的部分水解缩合物，固含量20%。",
        9: "具体实施方式\n以下结合实施例对本发明的水性硅烷前处理剂及其在铝合金表面处理中的应用作进一步说明。",
        10: "实施例一\n采用表3的组分配比制备前处理液，铝合金A356基材浸渍60 s后烘干。",
        11: "表3 前处理剂组成\n前体B 8% 10%\n氟锆酸 0.5% 0.6%\n去离子水 余量 余量",
        12: "实施例二\n同实施例一的工艺，将前体B提高到10%，氟锆酸提高到0.6%，铝合金A356基材浸渍60 s后120℃烘干10 min。",
        21: "表5 处理后铝合金的耐蚀性能\n实施例一 中性盐雾 720 h\n实施例二 中性盐雾 960 h",
        22: "表5中实施例二的耐蚀性能最优，中性盐雾960 h无腐蚀，说明提高前体B含量可显著改善膜层致密性。",
    })
    return SyntheticPatent(
        "pat-cn-renum", "水性硅烷前处理剂", pages, "table:3", "table:5", "前体B",
        {"实施例二的前处理剂组成与耐蚀性能": {"table:3", "table:5"}},
    )


def patent_en() -> SyntheticPatent:
    pages = {p: f"General description, page {p}, covering application, storage and handling." for p in range(1, 23)}
    pages.update({
        2: "Claims\n1. A coating composition comprising precursor C and a polyurethane binder.",
        4: "Summary of the Invention\nPrecursor C is defined as the reaction product of hexamethylene diisocyanate trimer "
           "with gamma-aminopropyltriethoxysilane at an NCO:NH ratio of 1:1.",
        8: "Detailed Description\nThe invention is further illustrated by the following non-limiting examples of silane-modified coatings.",
        9: "Example 1\nThe coating of Table 1 was sprayed on ADC12 die-cast aluminium and cured at 80 C.",
        10: "Table 1 Coating composition (parts by weight)\nPrecursor C 12\nPolyurethane binder 70\nSolvent 18",
        19: "Table 2 Test results of Example 1\nNeutral salt spray 1500 h\nCross-cut adhesion 0",
    })
    return SyntheticPatent(
        "pat-en", "Silane-modified polyurethane coating", pages, "table:1", "table:2", "precursor C",
        {"coating composition of example 1 and its salt spray test results": {"table:1", "table:2"}},
    )


ALL_PATENTS = (patent_cn_basic, patent_cn_renumbered, patent_en)
