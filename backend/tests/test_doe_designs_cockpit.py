"""M6: orthogonal arrays, constrained mixtures, D-optimal designs, DOE compliance screen, conformal
intervals, Pareto with hazard, and “设为定型”."""
from __future__ import annotations

import itertools
import math
import uuid
from datetime import datetime

import numpy as np
import pytest

from app.services.analysis import conformal as cf
from app.services.doe_designs import constrained_mixture as cm
from app.services.doe_designs import orthogonal_arrays as oa
from app.services.doe_designs.d_optimal import CategoricalFactor, ContinuousFactor, MixtureSpace, Model, select
from app.services.plm.ghs.hazard_score import hazard_score

# --------------------------------------------------------------------------- orthogonal arrays


@pytest.mark.parametrize("array", [a.name for a in oa.catalogue()])
def test_every_array_is_orthogonal(array):
    a = oa.get_array(array)
    assert len(a.rows) == a.runs and all(len(r) == a.columns for r in a.rows)
    assert oa.is_orthogonal(a)


def test_two_level_arrays_use_taguchi_column_numbers():
    l8 = oa.get_array("L8")
    assert [r[0] for r in l8.rows] == [0, 0, 0, 0, 1, 1, 1, 1]
    assert [r[3] for r in l8.rows] == [0, 1, 0, 1, 0, 1, 0, 1]
    one_based = lambda a, b: [c + 1 for c in l8.interaction_columns(a - 1, b - 1)]  # noqa: E731
    assert one_based(1, 2) == [3] and one_based(1, 4) == [5] and one_based(2, 4) == [6] and one_based(3, 4) == [7]
    l9 = oa.get_array("L9")
    assert [c + 1 for c in l9.interaction_columns(0, 1)] == [3, 4]  # a 3-level interaction takes two columns


def test_assignment_keeps_interaction_columns_clear():
    d = oa.design([oa.OAFactor(n, [1, 2]) for n in "ABCD"], interactions=[("A", "B"), ("A", "C"), ("B", "C")])
    assert d.array == "L8"
    cols = list(d.assignment.values()) + [c for v in d.interactions.values() for c in v]
    assert len(cols) == len(set(cols))  # no confounding among requested effects
    l8 = oa.get_array("L8")
    a, b = d.assignment["A"] - 1, d.assignment["B"] - 1
    assert d.interactions["A×B"] == [c + 1 for c in l8.interaction_columns(a, b)]
    # Each pair of factors sees each level combination twice in 8 runs.
    for x, y in itertools.combinations("ABCD", 2):
        combos = [(r[x], r[y]) for r in d.runs]
        assert all(combos.count(c) == 2 for c in set(combos)) and len(set(combos)) == 4


def test_array_choice_levels_and_errors():
    d = oa.design([oa.OAFactor(n, ["低", "中", "高"]) for n in "ABCD"])
    assert d.array == "L9" and {r["A"] for r in d.runs} == {"低", "中", "高"}
    d = oa.design([oa.OAFactor("X", [1, 2])] + [oa.OAFactor(n, [1, 2, 3]) for n in "ABCDE"])
    assert d.array == "L18"
    d = oa.design([oa.OAFactor("X", [1, 2])] + [oa.OAFactor(n, [1, 2, 3]) for n in "ABCDEFGH"])
    assert d.array == "L27" and d.dummy_levels  # 2-level factor on a 3-level column (dummy level)
    assert oa.design([oa.OAFactor(n, [1, 2, 3, 4]) for n in "ABC"]).array == "L16(4^5)"
    with pytest.raises(oa.OrthogonalError):
        oa.design([oa.OAFactor(n, [1, 2]) for n in "ABC"], interactions=[("A", "Z")])
    with pytest.raises(oa.OrthogonalError):
        oa.design([oa.OAFactor("A", [1, 2]), oa.OAFactor("B", [1, 2, 3])], interactions=[("A", "B")])
    with pytest.raises(oa.OrthogonalError):
        oa.design([oa.OAFactor(n, [1, 2, 3, 4, 5, 6]) for n in "AB"])


# --------------------------------------------------------------------------- mixtures & D-optimal


FLARE = [cm.Component("magnesium", 0.40, 0.60), cm.Component("sodium_nitrate", 0.10, 0.50),
         cm.Component("strontium_nitrate", 0.10, 0.50), cm.Component("binder", 0.03, 0.08)]


def test_extreme_vertices_of_the_mclean_anderson_flare_example():
    verts = cm.extreme_vertices(cm.check_bounds(FLARE, 1.0), 1.0)
    assert len(verts) == 8  # McLean & Anderson (1966): 8 extreme vertices
    # Independent check: solve every system "sum = 1 + three active bounds" and keep feasible points.
    bounds = [(i, side) for i in range(4) for side in (0, 1)]
    brute = set()
    for combo in itertools.combinations(bounds, 3):
        if len({i for i, _s in combo}) < 3:
            continue
        A = [[1.0] * 4] + [[1.0 if j == i else 0.0 for j in range(4)] for i, _s in combo]
        b = [1.0] + [FLARE[i].high if side else FLARE[i].low for i, side in combo]
        x = np.linalg.solve(np.array(A), np.array(b))
        if all(c.low - 1e-9 <= v <= c.high + 1e-9 for v, c in zip(x, FLARE, strict=True)):
            brute.add(tuple(round(float(v), 6) for v in x))
    assert brute == {tuple(round(v, 6) for v in vtx) for vtx in verts}
    for v in verts:
        assert math.isclose(sum(v), 1.0, abs_tol=1e-9)
        assert all(c.low - 1e-9 <= x <= c.high + 1e-9 for x, c in zip(v, FLARE, strict=True))
    assert (0.4, 0.47, 0.1, 0.03) in {tuple(round(x, 4) for x in v) for v in verts}


def test_candidates_and_design_respect_constraints():
    comps = [cm.Component("树脂", 40, 60), cm.Component("固化剂", 10, 25), cm.Component("溶剂", 15, 40),
             cm.Component("颜料", 5, 15)]
    cands = cm.candidate_points(comps, 100)
    kinds = {c["_kind"] for c in cands}
    assert {"vertex", "edge_midpoint", "facet_centroid", "centroid", "axial_check_blend"} <= kinds
    d = cm.design(comps, total=100, seed=3)
    assert d.model["parameters"] == 10 and len(d.runs) >= 10
    for r in d.runs:
        assert math.isclose(sum(r[c.name] for c in comps), 100, abs_tol=1e-4)
        assert all(c.low - 1e-6 <= r[c.name] <= c.high + 1e-6 for c in comps)
    assert d.runs[0]["_kind"] == "centroid"  # forced in
    assert d.model["d_efficiency_vs_candidates"] >= 100


def test_bounds_are_tightened_and_inconsistent_bounds_rejected():
    tight = cm.check_bounds([cm.Component("A", 0, 90), cm.Component("B", 30, 50), cm.Component("C", 10, 20)], 100)
    assert (tight[0].low, tight[0].high) == (30, 60)
    with pytest.raises(cm.MixtureError):
        cm.check_bounds([cm.Component("A", 60, 70), cm.Component("B", 50, 60)], 100)
    with pytest.raises(ValueError):
        cm.design(FLARE, total=1.0, n_runs=5, mixture_order="quadratic")  # 10 parameters


def test_fedorov_beats_random_and_finds_the_simplex_vertices():
    comps = [cm.Component(n, 0, 1) for n in "ABC"]
    d = cm.design(comps, total=1.0, n_runs=3, mixture_order="linear", include_centroid=False)
    assert sorted(tuple(round(r[n]) for n in "ABC") for r in d.runs) == [(0, 0, 1), (0, 1, 0), (1, 0, 0)]

    rng = np.random.default_rng(0)
    F = rng.normal(size=(60, 5))
    sel = select(F, 8, seed=1)
    best_random = max(np.linalg.slogdet(F[rng.choice(60, 8, replace=False)].T @ F[rng.choice(60, 8, replace=False)])[1]
                      for _ in range(1))
    randoms = []
    for _ in range(200):
        idx = rng.choice(60, 8, replace=False)
        randoms.append(np.linalg.slogdet(F[idx].T @ F[idx])[1])
    assert sel.log_det >= max(randoms) and sel.log_det >= best_random
    assert sel.max_prediction_variance >= sel.p - 1e-6


def test_mixture_process_crossing_and_categorical_coding():
    model = Model(mixture=MixtureSpace(["A", "B", "C"], [0, 0, 0], 1.0), mixture_order="quadratic",
                  continuous=[ContinuousFactor("T", 120, 180)], cross="full")
    row, terms = model.row({"A": 0.5, "B": 0.5, "C": 0.0, "T": 180})
    assert len(row) == 6 * 2 and "A·B×T" in terms
    assert row[terms.index("A·B×T")] == pytest.approx(0.25)
    d = cm.design([cm.Component("A", 0.2, 0.6), cm.Component("B", 0.2, 0.6), cm.Component("C", 0.1, 0.4)], total=1.0,
                  mixture_order="linear", process=[ContinuousFactor("T", 120, 180, levels=2)], seed=0)
    assert d.model["parameters"] == 6 and {r["T"] for r in d.runs} == {120, 180}
    cat = Model(categorical=[CategoricalFactor("树脂", ["E51", "E44", "E20"])])
    assert cat.row({"树脂": "E20"})[0] == [1.0, -1.0, -1.0]  # effects coding
    out = cm.factorial_d_optimal([ContinuousFactor("T", 100, 200), ContinuousFactor("t", 10, 30)],
                                 [CategoricalFactor("树脂", ["E51", "E44"])], order="quadratic", n_runs=10)
    assert len(out["runs"]) == 10 and out["model"]["parameters"] == 7


# --------------------------------------------------------------------------- conformal


def test_conformal_quantile_and_minimum_points():
    assert cf.quantile([1, 2, 3, 4, 5, 6, 7, 8, 9], 0.1) == 9  # ceil(10 × 0.9) = 9th smallest
    assert cf.quantile([1, 2, 3, 4, 5], 0.1) == math.inf  # too few for 90 %
    with pytest.raises(cf.ConformalError):
        cf.calibrate([cf.Pair(1, 1)] * 3)


def test_conformal_coverage_and_drift_alarm():
    rng = np.random.default_rng(42)
    preds = rng.uniform(100, 200, 400)
    pairs = [cf.Pair(float(p), float(p + rng.normal(0, 5))) for p in preds]
    mon = cf.monitor(pairs, 0.1)
    assert 0.86 <= mon["coverage"] <= 0.95 and not mon["alarm"]
    drifted = pairs[:200] + [cf.Pair(p.predicted, p.predicted + rng.normal(15, 5)) for p in pairs[200:]]
    mon = cf.monitor(drifted, 0.1)
    assert mon["alarm"] and mon["coverage"] < 0.85
    # Normalised scores adapt the width to sigma.
    hetero = [cf.Pair(0.0, float(rng.normal(0, s)), sigma=s) for s in rng.uniform(1, 10, 300)]
    cal = cf.calibrate(hetero, 0.1, normalized=True)
    assert 1.3 < cal["q"] < 2.1  # ≈ 1.645 for Gaussian residuals


def test_hazard_score_orders_classifications():
    mild = {"hazards": [{"hazard_class": "skin_corrosion", "category": "2"}]}
    severe = {"hazards": [{"hazard_class": "carcinogenicity", "category": "1B"},
                          {"hazard_class": "acute_tox_oral", "category": "3"}]}
    assert hazard_score({"hazards": []}) == 0 < hazard_score(mild) < hazard_score(severe)


# --------------------------------------------------------------------------- API


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings

    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'm6.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "m6-secret-" + "y" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow, FormulationVersion, MeasurementRow
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        for name, roles in {"lead": ["project_lead"], "rd": ["rd_engineer"]}.items():
            accounts.create_user(s, username=name, password="Fx-Cockpit-2026", roles=roles)
        xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", aliases=[], hazardous_catalog_no=2289)
        s.add_all([xyl, SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", aliases=[])])
        s.flush()
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class="skin_corrosion",
                                         category="2", h_code="H315", source="cn_catalog"))
        s.add(FormulationVersion(id="fv-9", lineage_id="l", version=1, domain="coating", name="v1",
                                 created_at=datetime(2026, 10, 1), snapshot={"ingredients": []}))
        exp = ExperimentRow(domain="coating", project_id="", factors={}, measured={"salt_spray_hours": 520})
        s.add(exp)
        s.flush()
        s.add(MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp.id, metric="salt_spray_hours", value=520,
                             review_status="reviewed"))
        s.commit()
        exp_id = exp.id
    from app.main import app

    client = TestClient(app)
    tokens = {n: {"Authorization": "Bearer " + client.post("/api/identity/login", json={
        "username": n, "password": "Fx-Cockpit-2026"}).json()["access_token"]} for n in ("lead", "rd")}
    yield client, tokens, exp_id
    get_settings.cache_clear()


def test_orthogonal_and_d_optimal_endpoints(api):
    client, _h, _e = api
    arrays = client.get("/api/doe/designs/orthogonal-arrays").json()
    assert {"L8", "L9", "L18", "L27"} <= {a["name"] for a in arrays}
    r = client.post("/api/doe/designs/orthogonal", json={
        "factors": [{"name": "固化温度", "levels": [120, 150, 180]}, {"name": "固化时间", "levels": [20, 30, 40]},
                    {"name": "树脂", "levels": ["E51", "E44", "E20"]}],
        "interactions": [["固化温度", "固化时间"]], "persist": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # 3 factors + one 3-level interaction (2 columns) need 5 columns: L9 has 4, so L27.
    assert body["array"] == "L27" and len(body["runs"]) == 27 and len(body["interactions"]["固化温度×固化时间"]) == 2
    assert body["plan"]["design"] == "orthogonal_L27" and len(body["plan"]["runs"]) == 27
    bad = client.post("/api/doe/designs/orthogonal", json={"factors": [{"name": "A", "levels": [1, 2]}],
                                                           "interactions": [["A", "B"]]})
    assert bad.status_code == 422
    r = client.post("/api/doe/designs/d-optimal", json={
        "process_factors": [{"name": "T", "low": 120, "high": 180}], "categorical_factors": [
            {"name": "固化剂", "levels": ["胺 A", "胺 B", "酸酐"]}], "order": "interactions", "n_runs": 9})
    assert r.status_code == 200 and len(r.json()["runs"]) == 9


def test_mixture_endpoint_screens_runs_against_red_lines(api):
    client, _h, _e = api
    body = {"components": [{"name": "二甲苯", "low": 2, "high": 40, "cas": "1330-20-7"},
                           {"name": "水", "low": 60, "high": 98, "cas": "7732-18-5"}],
            "mixture_order": "quadratic", "n_runs": 6,
            "screen": {"red_lines": {"forbidden_pictograms": ["GHS07"],
                                     "substances": [{"cas": "1330-20-7", "max_pct": 35, "name": "二甲苯",
                                                     "reason": "VOC 控制"}]}}}
    r = client.post("/api/doe/designs/mixture", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    by_run = {x["run"]: x for x in out["screen"]["runs"]}
    for run in out["runs"]:
        res = by_run[run["_run"]]
        assert res["ok"] == (run["二甲苯"] < 10), (run, res)  # skin irrit. 2 (GHS07) from 10 %
        if run["二甲苯"] > 35:
            assert any("VOC 控制" in m for m in res["reasons"])
    dropped = client.post("/api/doe/designs/mixture", json={**body, "screen": {**body["screen"], "drop_flagged": True}})
    assert all(r["二甲苯"] < 10 for r in dropped.json()["runs"])
    r = client.post("/api/doe/designs/mixture", json={**body, "fixed": [{"name": "颜料", "pct": 20}], "total": 90})
    assert r.status_code == 422  # 90 + 20 ≠ 100
    r = client.post("/api/doe/designs/screen", json={"components": body["components"], "runs": [{"二甲苯": 5}]})
    assert r.status_code == 422


def test_conformal_and_pareto_endpoints(api):
    client, _h, _e = api
    rng = np.random.default_rng(1)
    pairs = [{"predicted": float(p), "actual": float(p + rng.normal(0, 2))} for p in rng.uniform(10, 20, 60)]
    r = client.post("/api/cockpit/conformal", json={"metric": "adhesion_mpa", "pairs": pairs, "alpha": 0.1,
                                                     "predict": [{"predicted": 15.0, "id": "next"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    q = out["calibration"]["q"]
    assert out["intervals"][0]["lower"] == pytest.approx(15 - q) and out["monitor"]["evaluated"] == 55
    assert client.post("/api/cockpit/conformal", json={"pairs": pairs[:3]}).status_code == 422

    r = client.post("/api/cockpit/pareto", json={
        "objectives": [{"metric": "salt_spray_hours", "direction": "maximize"},
                       {"metric": "hazard_score", "direction": "minimize"}],
        "points": [
            {"id": "a", "values": {"salt_spray_hours": 500}, "composition": [
                {"name": "二甲苯", "pct": 30, "cas": "1330-20-7"}, {"name": "水", "pct": 70, "cas": "7732-18-5"}]},
            {"id": "b", "values": {"salt_spray_hours": 450}, "composition": [
                {"name": "二甲苯", "pct": 5, "cas": "1330-20-7"}, {"name": "水", "pct": 95, "cas": "7732-18-5"}]},
            {"id": "c", "values": {"salt_spray_hours": 400, "hazard_score": 5}},
        ]})
    assert r.status_code == 200, r.text
    pts = {p["id"]: p for p in r.json()["points"]}
    assert pts["a"]["hazard_score"] > 0 and pts["b"]["hazard_score"] == 0
    assert set(r.json()["frontier"]) == {"a", "b"} and pts["c"]["rank"] == 1


def test_winning_formulation_becomes_a_candidate_revision(api):
    client, h, exp_id = api
    pid = client.post("/api/plm/products", json={"code": "FX-200", "name_zh": "FX-200"}).json()["id"]
    client.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-9", "spec_snapshot": {"acceptance": [{"metric": "salt_spray_hours", "min": 480}],
                                                            "scratch": "not carried"}})
    body = {"product_id": pid, "formulation_version_id": "fv-9", "experiment_id": exp_id, "note": "第 3 轮最优"}
    assert client.post("/api/cockpit/winning", json=body).status_code == 401
    assert client.post("/api/cockpit/winning", json=body, headers=h["rd"]).status_code == 403
    r = client.post("/api/cockpit/winning", json=body, headers=h["lead"])
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["rev"] == "B" and out["state"] == "candidate" and out["winning_experiment_id"] == exp_id
    assert out["spec_snapshot"]["acceptance"][0]["min"] == 480 and "scratch" not in out["spec_snapshot"]
    assert out["spec_snapshot"]["winning"]["measured"] == {"salt_spray_hours": 520} and out["warnings"] == []
    gate = client.get(f"/api/plm/revisions/{out['revision_id']}/gate").json()
    objectives = next(c for c in gate["checks"] if c["key"] == "objectives")
    assert objectives["status"] == "pass"  # reviewed 520 h ≥ 480 h

    from app.db.database import default_session_factory
    from app.db.models_plm import LineageEdgeRow

    with default_session_factory()() as s:
        edges = {(e.from_type, e.relation) for e in s.query(LineageEdgeRow).filter_by(to_id=out["revision_id"])}
    assert edges == {("experiment", "selected_as_winning"), ("formulation_version", "frozen_as"),
                       ("product", "has_revision")}  # 2-22: winning also links product→revision
    assert client.post("/api/cockpit/winning", json={**body, "experiment_id": 999999}, headers=h["lead"]).status_code == 422
