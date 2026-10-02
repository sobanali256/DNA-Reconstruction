"""Unit tests for the confidence router (gates G3.1-G3.3)."""

import ast
from pathlib import Path

import pytest

import dnarecon.router as router_module
from dnarecon.router import route

CONF = {"a": 0.2, "b": 0.5, "c": 0.79, "d": 0.8, "e": 1.0, "f": None}


def test_g3_1_boundary_confidence_equal_to_tau_is_not_routed():
    assert route(CONF, 0.8) == ["a", "b", "c"]  # d (= tau) stays with BBS
    assert route({"x": 0.5}, 0.5) == [] and route({"x": 0.5}, 0.5000001) == ["x"]


def test_g3_3_tau_zero_routes_none_and_tau_one_all_but_confidence_one():
    assert route(CONF, 0) == []
    assert route(CONF, 1) == ["a", "b", "c", "d"]


def test_no_confidence_is_never_routed_and_order_is_kept():
    assert "f" not in route(CONF, 1)
    assert route({"z": 0.1, "y": 0.3, "x": 0.2}, 1) == ["z", "y", "x"]


@pytest.mark.parametrize("tau", [-0.1, 1.5])
def test_tau_outside_unit_interval_rejected(tau):
    with pytest.raises(ValueError):
        route(CONF, tau)


def test_g3_2_router_imports_nothing_that_carries_reads_or_truth():
    tree = ast.parse(Path(router_module.__file__).read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "typing"}
