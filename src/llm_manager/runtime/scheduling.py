"""Scheduling (adaptive select / resource check / eviction scoring). STUB — Plan 3."""
from __future__ import annotations


def compute_deficit(required, snap):
    raise NotImplementedError("Plan 3")


def check_and_free(required, snap, runnable, now, stop_fn):
    raise NotImplementedError("Plan 3")


def score_candidates(runnable_states, deficit_devs, now):
    raise NotImplementedError("Plan 3")
