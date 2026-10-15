"""Tests de carga y validación de configuración (config/*.yaml)."""

from datetime import time
from pathlib import Path

import pytest

from wti_news_alpha.config import (
    CONFIG_DIR,
    Param,
    SessionsConfig,
    load_project_config,
    load_yaml_model,
)
from wti_news_alpha.errors import SchemaError, UnverifiedValueError


def test_repository_config_loads() -> None:
    cfg = load_project_config(CONFIG_DIR)
    assert cfg.contracts.root_symbol == "CL"
    assert {s.name for s in cfg.sources.sources} >= {"gdelt_files", "eia_api", "fred_api"}


def test_unverified_market_specs_cannot_be_used() -> None:
    # F0: la web de CME no es accesible; nada de especificaciones de CL puede
    # usarse en cálculos hasta verificarse (CLAUDE.md §7).
    cfg = load_project_config(CONFIG_DIR)
    with pytest.raises(UnverifiedValueError):
        cfg.contracts.tick_size_usd_per_bbl.require("tick_size")
    with pytest.raises(UnverifiedValueError):
        cfg.sessions.daily_close_et.require("daily_close")


@pytest.mark.parametrize("status", ["verified", "approved"])
def test_param_require_returns_usable_value(status: str) -> None:
    p = Param[float].model_validate({"value": 0.01, "status": status, "source_ref": "x"})
    assert p.require("tick") == 0.01


@pytest.mark.parametrize("status", ["unverified", "proposed"])
def test_param_require_rejects_unusable_status(status: str) -> None:
    p = Param[float].model_validate({"value": 0.01, "status": status, "source_ref": "x"})
    with pytest.raises(UnverifiedValueError, match="tick"):
        p.require("tick")


def test_param_require_rejects_missing_value_even_if_approved() -> None:
    p = Param[float].model_validate({"value": None, "status": "approved", "source_ref": "x"})
    with pytest.raises(UnverifiedValueError, match="sin valor"):
        p.require("commission")


def test_param_rejects_empty_source_ref() -> None:
    with pytest.raises(ValueError, match="source_ref"):
        Param[float].model_validate({"value": 1.0, "status": "verified", "source_ref": ""})


def _write_sessions(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "sessions.yaml"
    param = '{value: "17:00", status: unverified, source_ref: test}'
    path.write_text(
        "timezone: America/New_York\n"
        f"weekly_open_et: {{value: {{weekday: sunday, time: '18:00'}}, "
        "status: unverified, source_ref: test}\n"
        f"daily_close_et: {param}\n"
        f"daily_reopen_et: {param}\n"
        f"settlement_time_et: {param}\n" + extra,
        encoding="utf-8",
    )
    return path


def test_load_yaml_model_parses_times(tmp_path: Path) -> None:
    cfg = load_yaml_model(_write_sessions(tmp_path), SessionsConfig)
    assert cfg.daily_close_et.value == time(17, 0)


def test_load_yaml_model_rejects_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match=r"sessions\.yaml"):
        load_yaml_model(_write_sessions(tmp_path, "unexpected: 1\n"), SessionsConfig)


def test_load_yaml_model_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="mapping"):
        load_yaml_model(path, SessionsConfig)


def test_load_yaml_model_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="no existe"):
        load_yaml_model(tmp_path / "missing.yaml", SessionsConfig)


def test_sessions_timezone_must_be_new_york(tmp_path: Path) -> None:
    path = _write_sessions(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("America/New_York", "Europe/Madrid"),
        encoding="utf-8",
    )
    with pytest.raises(SchemaError):
        load_yaml_model(path, SessionsConfig)
