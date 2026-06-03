"""Tests for the benchmark runner's language/opt selection helpers."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "bench_runner", Path(__file__).resolve().parent.parent / "benchmarks" / "runner.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def test_select_langs_filters_by_name():
    selected = runner.select_langs(["C", "Rolang"])
    names = [l.name for l in selected]
    assert names == ["C", "Rolang"]


def test_select_langs_none_returns_all():
    assert len(runner.select_langs(None)) == len(runner.LANGS)


def test_select_langs_is_case_insensitive():
    assert [l.name for l in runner.select_langs(["rolang"])] == ["Rolang"]


def test_select_langs_unknown_name_skipped():
    assert [l.name for l in runner.select_langs(["Nope", "C"])] == ["C"]


def test_select_langs_preserves_langs_order():
    # input order reversed; output must follow LANGS order
    assert [l.name for l in runner.select_langs(["Rolang", "C"])] == ["C", "Rolang"]
