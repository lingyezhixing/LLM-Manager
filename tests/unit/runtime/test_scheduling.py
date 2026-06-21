from llm_manager.devices import DeviceInfo
from llm_manager.runtime.scheduling import (
    RunnableInfo, compute_deficit, score_candidates, check_and_free,
)


def test_compute_deficit_no_gap_when_sufficient():
    assert compute_deficit({"rtx 4060": 2048}, {"rtx 4060": 4096}) == {}


def test_compute_deficit_reports_gap():
    assert compute_deficit({"rtx 4060": 4096}, {"rtx 4060": 1024}) == {"rtx 4060": 3072}


def test_compute_deficit_missing_device_full_required():
    assert compute_deficit({"rtx 4060": 4096}, {}) == {"rtx 4060": 4096}


def test_runnable_info_is_frozen():
    ri = RunnableInfo(mem_mb={"d": 1024}, pending=0, last_access=1.0)
    assert ri.mem_mb == {"d": 1024} and ri.pending == 0


def test_score_orders_by_idle_per_mem_descending():
    now = 1000.0
    runnable = {
        "a": RunnableInfo(mem_mb={"d": 1024}, pending=0, last_access=900.0),   # idle100/1GB=100
        "b": RunnableInfo(mem_mb={"d": 2048}, pending=0, last_access=950.0),   # idle50/2GB=25
        "c": RunnableInfo(mem_mb={"d": 512}, pending=0, last_access=800.0),    # idle200/0.5GB(floor)=400
    }
    assert score_candidates(runnable, {"d"}, now) == ["c", "a", "b"]


def test_score_excludes_pending():
    now = 1000.0
    runnable = {
        "a": RunnableInfo(mem_mb={"d": 1024}, pending=0, last_access=0.0),
        "b": RunnableInfo(mem_mb={"d": 1024}, pending=1, last_access=0.0),
    }
    assert score_candidates(runnable, {"d"}, now) == ["a"]


def test_score_mem_floor_applies_floor_for_tiny_mem():
    now = 1000.0
    # 1MB occupancy → mem_gb = max(0.5, 1/1024) = 0.5 (floor); occ>0 so still a
    # candidate, and no div-by-zero. (occ==0 models are excluded as evicting
    # them frees nothing — covered by test_score_only_models_on_deficit_devices.)
    runnable = {"a": RunnableInfo(mem_mb={"d": 1}, pending=0, last_access=900.0)}
    assert score_candidates(runnable, {"d"}, now) == ["a"]


def test_score_only_models_on_deficit_devices():
    now = 1000.0
    runnable = {
        "a": RunnableInfo(mem_mb={"d1": 1024}, pending=0, last_access=0.0),
        "b": RunnableInfo(mem_mb={"d2": 1024}, pending=0, last_access=0.0),
    }
    assert score_candidates(runnable, {"d1"}, now) == ["a"]


def _dev(name, avail):
    return DeviceInfo(name, "GPU", "VRAM", 4096, avail, 4096 - avail, 0.0, None)


def test_check_and_free_no_eviction_when_no_deficit():
    snap = {"d": _dev("d", 4096)}
    assert check_and_free({"d": 1024}, snap, {}, now=0.0) == []


def test_check_and_free_evicts_until_satisfied():
    snap = {"d": _dev("d", 0)}    # avail 0, need 4096
    runnable = {
        "a": RunnableInfo(mem_mb={"d": 2048}, pending=0, last_access=0.0),    # idle1000/2=500
        "b": RunnableInfo(mem_mb={"d": 2048}, pending=0, last_access=100.0),  # idle900/2=450
    }
    assert check_and_free({"d": 4096}, snap, runnable, now=1000.0) == ["a", "b"]


def test_check_and_free_stops_as_soon_as_satisfied():
    # score = idle_sec / mem_gb desc. b: 900/2.0=450 > a: 1000/4.0=250, so b is
    # evicted first; one eviction frees 2048 == deficit 2048 → stop early.
    snap = {"d": _dev("d", 0)}
    runnable = {
        "a": RunnableInfo(mem_mb={"d": 4096}, pending=0, last_access=0.0),
        "b": RunnableInfo(mem_mb={"d": 2048}, pending=0, last_access=100.0),
    }
    assert check_and_free({"d": 2048}, snap, runnable, now=1000.0) == ["b"]


def test_check_and_free_never_evicts_pending():
    snap = {"d": _dev("d", 0)}
    runnable = {"a": RunnableInfo(mem_mb={"d": 4096}, pending=1, last_access=0.0)}
    assert check_and_free({"d": 4096}, snap, runnable, now=0.0) == []


def test_check_and_free_returns_empty_when_no_evictable():
    snap = {"d": _dev("d", 0)}
    assert check_and_free({"d": 4096}, snap, {}, now=0.0) == []
