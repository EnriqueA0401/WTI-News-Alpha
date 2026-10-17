"""Tests de carga y validación de configuración (config/*.yaml)."""

import re
from datetime import UTC, time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from wti_news_alpha.config import (
    CONFIG_DIR,
    ContractsConfig,
    CostsConfig,
    HorizonsConfig,
    HorizonSpec,
    Param,
    SessionsConfig,
    SourcesConfig,
    SourceSpec,
    SplitsConfig,
    WallTime,
    load_project_config,
    load_yaml_model,
)
from wti_news_alpha.errors import SchemaError, UnverifiedValueError

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "wti_news_alpha"


def test_repository_config_loads() -> None:
    cfg = load_project_config(CONFIG_DIR)
    assert cfg.contracts.root_symbol == "CL"
    assert {s.name for s in cfg.sources.sources} >= {"gdelt_files", "eia_api", "fred_api"}


def test_contract_specs_verified_against_rulebook() -> None:
    cfg = load_project_config(CONFIG_DIR)
    size = cfg.contracts.contract_size_bbl.require("contract_size")
    tick = cfg.contracts.tick_size_usd_per_bbl.require("tick_size")
    assert size == 1000
    assert tick == 0.01
    # Coherencia: valor del tick = tamaño x tick.
    assert cfg.contracts.tick_value_usd.require("tick_value") == pytest.approx(size * tick)


def test_unverified_session_times_cannot_be_used() -> None:
    # El horario no está en el Rulebook y la Fact Card es de 2012 (ADR-010):
    # no puede usarse en cálculos hasta verificarse.
    cfg = load_project_config(CONFIG_DIR)
    with pytest.raises(UnverifiedValueError):
        cfg.sessions.daily_close_et.require("daily_close")
    with pytest.raises(UnverifiedValueError):
        cfg.sessions.settlement_time_et.require("settlement")


def test_src_never_reads_param_value_directly() -> None:
    # Regresión (revisión adversarial): el gate de estado no debe poder saltarse.
    offenders = [
        p.relative_to(SRC_DIR).as_posix()
        for p in SRC_DIR.rglob("*.py")
        if p.name != "config.py" and re.search(r"\bunsafe_value\b", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# ---------------------------------------------------------------- Param


def _param(value: Any, status: str = "verified", source_ref: str = "x") -> dict[str, Any]:
    return {"value": value, "status": status, "source_ref": source_ref}


@pytest.mark.parametrize("status", ["verified", "approved"])
def test_param_require_returns_usable_value(status: str) -> None:
    p = Param[float].model_validate(_param(0.01, status))
    assert p.require("tick") == 0.01


@pytest.mark.parametrize("status", ["unverified", "proposed"])
def test_param_require_rejects_unusable_status(status: str) -> None:
    p = Param[float].model_validate(_param(0.01, status))
    with pytest.raises(UnverifiedValueError, match="tick"):
        p.require("tick")


def test_param_require_rejects_missing_value_even_if_approved() -> None:
    p = Param[float].model_validate(_param(None, "approved"))
    with pytest.raises(UnverifiedValueError, match="sin valor"):
        p.require("commission")


@pytest.mark.parametrize("ref", ["", "   "])
def test_param_rejects_empty_source_ref(ref: str) -> None:
    with pytest.raises(ValidationError, match="source_ref"):
        Param[float].model_validate(_param(1.0, source_ref=ref))


def _contracts(**override: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root_symbol": "CL",
        "exchange": "NYMEX",
        "contract_size_bbl": _param(1000),
        "tick_size_usd_per_bbl": _param(0.01),
        "tick_value_usd": _param(10.0),
        "last_trade_rule": _param("regla"),
        "roll_rule": _param(None, "proposed"),
    }
    base.update(override)
    return base


def test_contracts_valid() -> None:
    cfg = ContractsConfig.model_validate(_contracts())
    assert cfg.tick_size_usd_per_bbl.require("tick") == 0.01


@pytest.mark.parametrize("bad", [float("nan"), float("-inf"), True, "0.01", -0.01, 0.0])
def test_contracts_reject_nonsense_tick(bad: object) -> None:
    # Regresión (revisión adversarial): NaN, bool, texto y negativos se aceptaban.
    with pytest.raises(ValidationError):
        ContractsConfig.model_validate(_contracts(tick_size_usd_per_bbl=_param(bad)))


def _costs(**override: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "currency": "USD",
        "commission_usd_per_side": _param(None, "unverified"),
        "spread_ticks": _param(None, "unverified"),
        "spread_source": _param("measured", "approved"),
        "tick_size_usd": _param(0.01, "approved"),
        "slippage_ticks_per_side": _param(1.0, "proposed"),
        "latency_seconds": _param(None, "proposed"),
        "sensitivity_multipliers": _param([1.0, 2.0, 3.0], "proposed"),
    }
    base.update(override)
    return base


def test_costs_reject_negative_and_return_immutable() -> None:
    with pytest.raises(ValidationError):
        CostsConfig.model_validate(_costs(slippage_ticks_per_side=_param(-5.0)))
    cfg = CostsConfig.model_validate(_costs(sensitivity_multipliers=_param([1.0, 2.0])))
    assert isinstance(cfg.sensitivity_multipliers.require("mult"), tuple)


# ---------------------------------------------------------------- horizons / splits


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "a", "kind": "market_minutes"},
        {"name": "a", "kind": "session_end", "length": 5},
        {"name": "a", "kind": "sessions", "length": 0},
    ],
)
def test_horizon_spec_rejects_inconsistent(spec: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HorizonSpec.model_validate(spec)


def test_horizons_reject_duplicate_names(tmp_path: Path) -> None:
    path = tmp_path / "horizons.yaml"
    path.write_text(
        "horizons: {value: [{name: a, kind: session_end}, {name: a, kind: session_end}],"
        " status: proposed, source_ref: t}\n"
        "time_basis: {value: market_time, status: proposed, source_ref: t}\n"
        "session_end_reference: {value: settlement, status: proposed, source_ref: t}\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="duplicados"):
        load_yaml_model(path, HorizonsConfig)


def _splits(train: float | None, val: float | None, test: float | None) -> dict[str, Any]:
    return {
        "train_fraction": _param(train, "proposed"),
        "validation_fraction": _param(val, "proposed"),
        "test_fraction": _param(test, "proposed"),
        "embargo": _param(None, "proposed"),
        "fold_step": _param(None, "proposed"),
        "sealed_test_window": _param(None, "proposed"),
    }


def test_splits_must_sum_to_one() -> None:
    SplitsConfig.model_validate(_splits(0.65, 0.175, 0.175))
    SplitsConfig.model_validate(_splits(0.65, None, 0.175))  # incompleto: sin comprobar suma
    with pytest.raises(ValidationError, match="sumar 1"):
        SplitsConfig.model_validate(_splits(0.6, 0.2, 0.3))
    with pytest.raises(ValidationError):
        SplitsConfig.model_validate(_splits(1.5, 0.2, 0.3))


# ---------------------------------------------------------------- sources


def _source(**override: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "s",
        "description": "d",
        "docs_url": "https://example.org/docs",
        "terms_url": None,
        "probe_url": "https://example.org/probe",
        "auth": "none",
    }
    base.update(override)
    return base


@pytest.mark.parametrize(
    "override",
    [
        {"auth": "query_param"},
        {"auth": "query_param", "auth_env": "K"},
        {"auth": "none", "auth_env": "K"},
        {"auth": "basic_user", "auth_env": "K", "auth_param": "api_key"},
        {"probe_url": "https://x.org/?api_key=SECRET"},
        {"probe_url": "http://x.org/"},
        {"probe_url": "not a url"},
    ],
)
def test_source_spec_rejects_inconsistent(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SourceSpec.model_validate(_source(**override))


def test_sources_reject_duplicate_names() -> None:
    with pytest.raises(ValidationError, match="duplicados"):
        SourcesConfig.model_validate({"sources": [_source(), _source()]})


# ---------------------------------------------------------------- YAML loading


def _write_sessions(tmp_path: Path, close: str = '"17:00"', extra: str = "") -> Path:
    path = tmp_path / "sessions.yaml"
    param = '{value: "18:00", status: unverified, source_ref: test}'
    path.write_text(
        "timezone: America/New_York\n"
        "weekly_open_et: {value: {weekday: sunday, time: '18:00'}, "
        "status: unverified, source_ref: test}\n"
        f"daily_close_et: {{value: {close}, status: unverified, source_ref: test}}\n"
        f"daily_reopen_et: {param}\n"
        f"settlement_time_et: {param}\n" + extra,
        encoding="utf-8",
    )
    return path


def test_load_yaml_model_parses_times(tmp_path: Path) -> None:
    cfg = load_yaml_model(_write_sessions(tmp_path), SessionsConfig)
    assert cfg.daily_close_et.unsafe_value == time(17, 0)


@pytest.mark.parametrize("close", ["17:00", "61200", "1.5", '"17:00Z"', '"17:00-05:00"', '"5pm"'])
def test_wall_time_rejects_ambiguous_yaml(tmp_path: Path, close: str) -> None:
    # Regresión (revisión adversarial): YAML lee 17:00 sin comillas como 1020
    # (base 60) y pydantic lo convertía en 00:17 UTC.
    with pytest.raises(SchemaError, match="hora de pared"):
        load_yaml_model(_write_sessions(tmp_path, close=close), SessionsConfig)


def test_load_yaml_model_rejects_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match=r"sessions\.yaml"):
        load_yaml_model(_write_sessions(tmp_path, extra="unexpected: 1\n"), SessionsConfig)


def test_load_yaml_model_rejects_duplicate_keys(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): 'status' duplicado se quedaba con el último.
    path = tmp_path / "dup.yaml"
    path.write_text(
        "daily_close_et:\n  value: '17:00'\n  status: unverified\n  status: verified\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="duplicadas"):
        load_yaml_model(path, SessionsConfig)


def test_load_yaml_model_rejects_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="YAML inválido"):
        load_yaml_model(path, SessionsConfig)


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


def test_wall_time_python_objects() -> None:
    p = Param[WallTime].model_validate(_param(time(14, 30)))
    assert p.require("settlement") == time(14, 30)
    with pytest.raises(ValidationError, match="zona horaria"):
        Param[WallTime].model_validate(_param(time(14, 30, tzinfo=UTC)))
