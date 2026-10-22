"""Pre-seeded compliance lists (补齐计划 Phase 1D, 1-16).

Two authoritative Chinese lists, baked in as data (not fetched at runtime):
- ``new_pollutants_2023`` — 《重点管控新污染物清单（2023年版）》, MEE et al.
  部令第28号 (2022-12-29, effective 2023-03-01). 14 entries. Transcribed from
  the official MEE PDF (mee.gov.cn/xxgk2018/xxgk/xxgk02/202212/t20221230_1009167.html).
- ``priority_controlled`` — 《优先控制化学品名录》, MEE + MIIT + NHC.
  Batch 1 (PC001–PC022, 2017-12-29) and batch 2 (PC023–PC040, 公告 2020年 第47号,
  2020-10-30). Transcribed from the Wikipedia reproduction of the MEE
  announcements (which cite mee.gov.cn).

Data provenance / verification:
- Batch 1 of the priority list is CROSS-VALIDATED: all 10 substance groups it
  shares with the new-pollutants PDF (PFOS, SCCPs, HBCD, PFOA, pentachlorophenol,
  hexachlorobutadiene, decabromodiphenyl ether, dichloromethane, trichloromethane,
  nonylphenol) match the official PDF exactly, CAS for CAS.
- Batch 2 (``verified=False`` on its entries) could NOT be re-verified against
  the official PDF in this run (the mirror fetch timed out). It needs a one-time
  EHS cross-check against 公告 2020年 第47号 before it is relied on for
  enforcement. The entries are still usable — the transcription source is the
  same page whose batch-1 data validated exactly — but the flag says so.

Matching semantics (honest limits):
- Matching is by CAS only. A list entry with several CAS numbers is a CLASS:
  the check sums the composition over all its CAS numbers and compares the
  class total against ``max_pct`` (``max_pct=0`` = banned: any presence hits).
- Entries with NO CAS (``NP-13`` antibiotics, ``PC029`` dioxins/furans,
  ``PC036`` cyanides) cannot match by CAS and are listed for reference only;
  the check skips them (but counts them in evidence).
- A red line whose ``list_id`` / ``list_entry_id`` does not resolve is
  fail-closed (P1-24): callers record a blocking failure instead of
  silently skipping the red line, so a typo can never waive a ban.
- Entries that list only a representative CAS (e.g. ``PC007`` cadmium 7440-43-9
  for "镉及镉化合物", ``PC010`` potassium dichromate for hexavalent chromium)
  match only that CAS. Full class coverage (every cadmium compound) needs the
  substance database to carry the class linkage — out of scope for 1-16.

Red lines reference a list entry by ``{"list_id": ..., "list_entry_id": ...,
"max_pct": ...}`` inside the spec contract's ``red_lines`` array (see
:func:`resolve_list_reference`). Direct ``{"cas": ..., "max_pct": ...}``
entries keep working as before.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ListEntry:
    entry_id: str
    name: str
    cas_numbers: tuple[str, ...] = ()
    note: str = ""
    verified: bool = True


@dataclass(frozen=True)
class ComplianceList:
    list_id: str
    title: str
    authority: str
    reference: str
    effective: str
    entries: tuple[ListEntry, ...] = ()


# ── 重点管控新污染物清单 (2023年版) — MEE 部令第28号 ─────────────────────
NEW_POLLUTANTS_2023 = ComplianceList(
    list_id="new_pollutants_2023",
    title="重点管控新污染物清单（2023年版）",
    authority="生态环境部、工业和信息化部、农业农村部、商务部、海关总署、国家市场监督管理总局",
    reference="部令第28号 (2022-12-29)",
    effective="2023-03-01",
    entries=(
        ListEntry("NP-01", "全氟辛基磺酸及其盐类和全氟辛基磺酰氟 (PFOS类)",
                  ("1763-23-1", "307-35-7", "2795-39-3", "29457-72-5", "29081-56-9",
                   "70225-14-8", "56773-42-3", "251099-16-8")),
        ListEntry("NP-02", "全氟辛酸及其盐类和相关化合物 (PFOA类)", ("335-67-1",),
                  note="仅列代表性 CAS（全氟辛酸）；盐类与相关化合物按清单定义涵盖"),
        ListEntry("NP-03", "十溴二苯醚", ("1163-19-5",)),
        ListEntry("NP-04", "短链氯化石蜡",
                  ("85535-84-8", "68920-70-7", "71011-12-6", "85536-22-7", "85681-73-8", "108171-26-2")),
        ListEntry("NP-05", "六氯丁二烯", ("87-68-3",)),
        ListEntry("NP-06", "五氯苯酚及其盐类和酯类",
                  ("87-86-5", "131-52-2", "27735-64-4", "3772-94-9", "1825-21-4")),
        ListEntry("NP-07", "三氯杀螨醇", ("115-32-2",)),
        ListEntry("NP-08", "全氟己基磺酸及其盐类和相关化合物 (PFHxS类)", ("355-46-4",),
                  note="仅列代表性 CAS（全氟己基磺酸）；盐类与相关化合物按清单定义涵盖"),
        ListEntry("NP-09", "得克隆", ("13560-89-9", "135821-03-3", "135821-74-8")),
        ListEntry("NP-10", "二氯甲烷", ("75-09-2",)),
        ListEntry("NP-11", "三氯甲烷", ("67-66-3",)),
        ListEntry("NP-12", "壬基酚", ("25154-52-3", "84852-15-3")),
        ListEntry("NP-13", "抗生素类", (), note="类别条目，无单一 CAS；按名称引用，不参与 CAS 匹配"),
        ListEntry("NP-14", "淘汰类",
                  ("25637-99-4", "3194-55-6", "134237-50-6", "134237-51-7", "134237-52-8",
                   "57-74-9", "2385-85-5", "118-74-1", "50-29-3", "319-84-6", "319-85-7",
                   "58-89-9", "115-29-7", "959-98-8", "33213-65-9", "1031-07-8"),
                  note="六溴环十二烷、氯丹、灭蚁灵、六氯苯、滴滴涕、α/β-六氯环己烷、林丹、硫丹及异构体；多氯联苯为类别，无单一 CAS"),
    ),
)

# ── 优先控制化学品名录 (第一批 + 第二批) ──────────────────────────────────
_PRIORITY_BATCH1 = (
    ListEntry("PC001", "1,2,4-三氯苯", ("120-82-1",)),
    ListEntry("PC002", "1,3-丁二烯", ("106-99-0",)),
    ListEntry("PC003", "5-叔丁基-2,4,6-三硝基间二甲苯（二甲苯麝香）", ("81-15-2",)),
    ListEntry("PC004", "N,N'-二甲苯基对苯二胺", ("27417-40-9",), note="未指定甲基取代位"),
    ListEntry("PC005", "短链氯化石蜡",
              ("85535-84-8", "68920-70-7", "71011-12-6", "85536-22-7", "85681-73-8", "108171-26-2")),
    ListEntry("PC006", "二氯甲烷", ("75-09-2",)),
    ListEntry("PC007", "镉及镉化合物", ("7440-43-9",), note="仅列代表性 CAS（镉元素）；镉化合物需按类别另行覆盖"),
    ListEntry("PC008", "汞及汞化合物", ("7439-97-6",), note="仅列代表性 CAS（汞元素）"),
    ListEntry("PC009", "甲醛", ("50-00-0",)),
    ListEntry("PC010", "六价铬化合物", ("7778-50-9",), note="仅列代表性 CAS（重铬酸钾）"),
    ListEntry("PC011", "六氯代-1,3-环戊二烯", ("77-47-4",)),
    ListEntry("PC012", "六溴环十二烷",
              ("25637-99-4", "3194-55-6", "134237-50-6", "134237-51-7", "134237-52-8")),
    ListEntry("PC013", "萘", ("91-20-3",)),
    ListEntry("PC014", "铅化合物", ("7446-14-2",), note="仅列代表性 CAS（硫酸铅）"),
    ListEntry("PC015", "全氟辛基磺酸及其盐类和全氟辛基磺酰氟",
              ("1763-23-1", "307-35-7", "2795-39-3", "29457-72-5", "29081-56-9",
               "70225-14-8", "56773-42-3", "251099-16-8")),
    ListEntry("PC016", "壬基酚及壬基酚聚氧乙烯醚", ("25154-52-3", "84852-15-3", "9016-45-9")),
    ListEntry("PC017", "三氯甲烷", ("67-66-3",)),
    ListEntry("PC018", "三氯乙烯", ("79-01-6",)),
    ListEntry("PC019", "砷及砷化合物", ("7440-38-2",), note="仅列代表性 CAS（砷元素）"),
    ListEntry("PC020", "十溴二苯醚", ("1163-19-5",)),
    ListEntry("PC021", "四氯乙烯", ("127-18-4",)),
    ListEntry("PC022", "乙醛", ("75-07-0",)),
)
_PRIORITY_BATCH2 = (
    ListEntry("PC023", "1,1-二氯乙烯", ("75-35-4",), verified=False),
    ListEntry("PC024", "1,2-二氯丙烷", ("78-87-5",), verified=False),
    ListEntry("PC025", "2,4-二硝基甲苯", ("121-14-2",), verified=False),
    ListEntry("PC026", "2,4,6-三叔丁基苯酚", ("732-26-3",), verified=False),
    ListEntry("PC027", "苯", ("71-43-2",), verified=False),
    ListEntry("PC028", "多环芳烃类物质",
              ("56-55-3", "218-01-9", "50-32-8", "205-99-2", "207-08-9", "120-12-7", "53-70-3"),
              note="苯并[a]蒽、䓛、苯并[a]芘、苯并[b]荧蒽、苯并[k]荧蒽、蒽、二苯并[a,h]蒽",
              verified=False),
    ListEntry("PC029", "多氯二苯并对二噁英和多氯二苯并呋喃", (),
              note="类别条目，无单一 CAS；按名称引用，不参与 CAS 匹配", verified=False),
    ListEntry("PC030", "甲苯", ("108-88-3",), verified=False),
    ListEntry("PC031", "邻甲苯胺", ("95-53-4",), verified=False),
    ListEntry("PC032", "磷酸三(2-氯乙基)酯", ("115-96-8",), verified=False),
    ListEntry("PC033", "六氯丁二烯", ("87-68-3",), verified=False),
    ListEntry("PC034", "氯苯类物质", ("608-93-5", "118-74-1"), note="五氯苯、六氯苯", verified=False),
    ListEntry("PC035", "全氟辛酸（PFOA）及其盐类和相关化合物", ("335-67-1",),
              note="仅列代表性 CAS（全氟辛酸）", verified=False),
    ListEntry("PC036", "氰化物", (),
              note="氢氰酸、全部简单氰化物和锌氰络合物；不包括铁氰/亚铁氰/铜氰/镍氰/钴氰络合物。无单一 CAS，不参与 CAS 匹配",
              verified=False),
    ListEntry("PC037", "铊及铊化合物", ("7440-28-0",), note="仅列代表性 CAS（铊元素）", verified=False),
    ListEntry("PC038", "五氯苯酚及其盐类和酯类",
              ("87-86-5", "131-52-2", "27735-64-4", "3772-94-9", "1825-21-4"), verified=False),
    ListEntry("PC039", "五氯苯硫酚", ("133-49-3",), verified=False),
    ListEntry("PC040", "异丙基苯酚磷酸酯", ("68937-41-7",), verified=False),
)

PRIORITY_CONTROLLED = ComplianceList(
    list_id="priority_controlled",
    title="优先控制化学品名录（第一批 + 第二批）",
    authority="生态环境部、工业和信息化部、国家卫生健康委员会",
    reference="第一批 (2017-12-29)；第二批 公告 2020年 第47号 (2020-10-30)",
    effective="第一批 2017-12-29；第二批 2020-11-02",
    entries=_PRIORITY_BATCH1 + _PRIORITY_BATCH2,
)

ALL_LISTS: tuple[ComplianceList, ...] = (NEW_POLLUTANTS_2023, PRIORITY_CONTROLLED)
_BY_ID: dict[str, ComplianceList] = {lst.list_id: lst for lst in ALL_LISTS}


def get_list(list_id: str) -> ComplianceList | None:
    return _BY_ID.get(list_id)


def get_entry(list_id: str, entry_id: str) -> ListEntry | None:
    lst = _BY_ID.get(list_id)
    if lst is None:
        return None
    for entry in lst.entries:
        if entry.entry_id == entry_id:
            return entry
    return None


def resolve_list_reference(ref: dict) -> tuple[ListEntry | None, tuple[str, ...]]:
    """Resolve a red-line list reference ``{"list_id", "list_entry_id"}`` to (entry, CAS tuple).

    Returns (None, ()) when the reference is missing / unknown. Callers must
    NOT treat that as "no constraint": an unknown reference is fail-closed
    (see :func:`unresolved_redline_reason`) — the old fail-open reading of
    this return value was P1-24 and is removed.
    """
    entry = get_entry(str(ref.get("list_id") or ""), str(ref.get("list_entry_id") or ""))
    if entry is None:
        return None, ()
    return entry, entry.cas_numbers


def unresolved_redline_reason(rl: dict) -> str | None:
    """Fail-closed reason for a list-referenced red line that cannot be checked.

    Returns a human-readable reason when ``rl`` references a list / entry
    that does not exist (typo, renamed or deleted entry) — the red line
    cannot be verified, so callers must record a failure, not skip it.
    Returns None when the reference resolves, when it is a direct-CAS red
    line (no list fields), or when it resolves to a no-CAS class entry:
    those entries are documented reference-only (antibiotics / dioxins /
    cyanides cannot match by CAS) and are still skipped by design, but
    callers should count them in evidence instead of dropping them silently.
    """
    if not (rl.get("list_id") or rl.get("list_entry_id")):
        return None
    list_id = str(rl.get("list_id") or "")
    entry_id = str(rl.get("list_entry_id") or "")
    if get_list(list_id) is None:
        return (f"红线引用未解析，红线未生效：未知合规清单 {list_id!r} "
                f"（条目 {entry_id!r}），请修正引用后重新评估")
    if get_entry(list_id, entry_id) is None:
        return (f"红线引用未解析，红线未生效：清单 {list_id!r} 中无条目 {entry_id!r}，"
                f"请修正引用后重新评估")
    return None


def class_total_for_redline(pct_by_cas: dict[str, float], rl: dict) -> tuple[float, str] | None:
    """For a list-referenced red line, return (class total pct, display label).

    Returns None for direct-CAS red lines (caller uses its own check), for
    unknown references (callers must fail-closed via
    :func:`unresolved_redline_reason` — never skip), and for no-CAS class
    entries (antibiotics / dioxins / cyanides — cannot match by CAS).
    """
    if not (rl.get("list_id") or rl.get("list_entry_id")):
        return None
    lst = _BY_ID.get(str(rl.get("list_id") or ""))
    entry, cas_list = resolve_list_reference(rl)
    if entry is None or not cas_list:
        return None
    total = sum(pct_by_cas.get(c, 0.0) for c in cas_list)
    title = lst.title if lst is not None else str(rl.get("list_id"))
    return total, f"《{title}》{entry.name}"


def entry_to_dict(entry: ListEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "name": entry.name,
        "cas_numbers": list(entry.cas_numbers),
        "note": entry.note,
        "verified": entry.verified,
    }


def list_to_dict(lst: ComplianceList) -> dict:
    return {
        "list_id": lst.list_id,
        "title": lst.title,
        "authority": lst.authority,
        "reference": lst.reference,
        "effective": lst.effective,
        "entries": [entry_to_dict(e) for e in lst.entries],
    }
