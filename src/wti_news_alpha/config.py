"""Configuración declarativa validada con pydantic (``config/*.yaml``).

Cada parámetro externo o de diseño es un ``Param`` con ``status`` y
``source_ref``. Solo los estados ``verified`` (comprobado contra fuente
oficial) y ``approved`` (aprobado por el usuario) son utilizables en cálculos;
``Param.require`` lanza ``UnverifiedValueError`` en cualquier otro caso.
"""

from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wti_news_alpha.errors import SchemaError, UnverifiedValueError

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

ParamStatus = Literal["verified", "approved", "unverified", "proposed"]
USABLE_STATUSES: frozenset[str] = frozenset({"verified", "approved"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Param[T](_Strict):
    """Valor con trazabilidad: estado y referencia a la fuente o decisión."""

    value: T | None
    status: ParamStatus
    source_ref: str = Field(min_length=1)
    note: str = ""

    def require(self, name: str) -> T:
        """Devuelve el valor solo si es utilizable; si no, lanza ``UnverifiedValueError``."""
        if self.status not in USABLE_STATUSES:
            raise UnverifiedValueError(
                f"parámetro {name!r} con estado {self.status!r} no utilizable "
                f"(fuente: {self.source_ref})"
            )
        if self.value is None:
            raise UnverifiedValueError(f"parámetro {name!r} {self.status} pero sin valor")
        return self.value


# ------------------------------------------------------------------ sessions


class WeeklyTime(_Strict):
    weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    time: time


class SessionsConfig(_Strict):
    """Sesión de negociación CL. Horas de pared en America/New_York."""

    timezone: Literal["America/New_York"]
    weekly_open_et: Param[WeeklyTime]
    daily_close_et: Param[time]
    daily_reopen_et: Param[time]
    settlement_time_et: Param[time]


# ------------------------------------------------------------------ contracts


class ContractsConfig(_Strict):
    root_symbol: Literal["CL"]
    exchange: str
    contract_size_bbl: Param[int]
    tick_size_usd_per_bbl: Param[float]
    tick_value_usd: Param[float]
    roll_rule: Param[str]


# ------------------------------------------------------------------ horizons


class HorizonSpec(_Strict):
    name: str
    kind: Literal["market_minutes", "session_end", "sessions"]
    length: int | None = Field(default=None, gt=0)


class HorizonsConfig(_Strict):
    horizons: Param[list[HorizonSpec]]
    time_basis: Param[Literal["market_time", "clock_time"]]
    session_end_reference: Param[Literal["settlement", "globex_close"]]


# ------------------------------------------------------------------ costs


class CostsConfig(_Strict):
    currency: Literal["USD"]
    commission_usd_per_side: Param[float]
    spread_ticks: Param[float]
    slippage_ticks_per_side: Param[float]
    latency_seconds: Param[float]
    sensitivity_multipliers: Param[list[float]]


# ------------------------------------------------------------------ splits


class SplitsConfig(_Strict):
    train_fraction: Param[float]
    validation_fraction: Param[float]
    test_fraction: Param[float]
    embargo: Param[str]
    sealed_test_window: Param[str]


# ------------------------------------------------------------------ sources


class SourceSpec(_Strict):
    name: str
    description: str
    docs_url: str
    terms_url: str | None
    probe_url: str
    auth: Literal["none", "query_param", "basic_user"]
    auth_env: str | None = None
    auth_param: str | None = None


class SourcesConfig(_Strict):
    sources: list[SourceSpec]


# ------------------------------------------------------------------ project


class ProjectConfig(_Strict):
    sessions: SessionsConfig
    contracts: ContractsConfig
    horizons: HorizonsConfig
    costs: CostsConfig
    splits: SplitsConfig
    sources: SourcesConfig


def load_yaml_model[M: BaseModel](path: Path, model: type[M]) -> M:
    """Carga ``path`` y lo valida contra ``model``. Errores → ``SchemaError``."""
    if not path.is_file():
        raise SchemaError(f"el fichero de configuración {path} no existe")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SchemaError(f"{path.name}: se esperaba un mapping YAML en la raíz")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"{path.name}: configuración inválida\n{exc}") from exc


def load_project_config(config_dir: Path = CONFIG_DIR) -> ProjectConfig:
    """Carga y valida todos los ficheros de ``config_dir``."""
    return ProjectConfig(
        sessions=load_yaml_model(config_dir / "sessions.yaml", SessionsConfig),
        contracts=load_yaml_model(config_dir / "contracts.yaml", ContractsConfig),
        horizons=load_yaml_model(config_dir / "horizons.yaml", HorizonsConfig),
        costs=load_yaml_model(config_dir / "costs.yaml", CostsConfig),
        splits=load_yaml_model(config_dir / "splits.yaml", SplitsConfig),
        sources=load_yaml_model(config_dir / "sources.yaml", SourcesConfig),
    )
