"""Configuración declarativa validada con pydantic (``config/*.yaml``).

Cada parámetro externo o de diseño es un ``Param`` con ``status`` y
``source_ref``. Solo los estados ``verified`` (comprobado contra fuente
oficial) y ``approved`` (aprobado por el usuario) son utilizables en cálculos.
El valor se guarda como ``unsafe_value`` (clave YAML ``value``) y el código de
``src/`` solo debe leerlo mediante ``Param.require`` (lo comprueba un test).
"""

import itertools
import math
import re
from datetime import date, time
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    ValidationError,
    model_validator,
)

from wti_news_alpha.errors import SchemaError, UnverifiedValueError

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

ParamStatus = Literal["verified", "approved", "unverified", "proposed"]
USABLE_STATUSES: frozenset[str] = frozenset({"verified", "approved"})

# ------------------------------------------------------------------ tipos

_WALL_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


def _parse_wall_time(v: object) -> object:
    """Solo acepta 'HH:MM[:SS]' como texto (PyYAML lee 17:00 sin comillas como 1020)."""
    if isinstance(v, time):
        return v
    if not isinstance(v, str) or not _WALL_TIME_RE.match(v):
        raise ValueError(f"hora de pared inválida {v!r}: usa texto 'HH:MM' entre comillas")
    return time.fromisoformat(v)


def _reject_tz(v: time) -> time:
    if v.tzinfo is not None:
        raise ValueError("la hora de pared no debe llevar zona horaria")
    return v


WallTime = Annotated[time, BeforeValidator(_parse_wall_time), AfterValidator(_reject_tz)]
PositiveFloat = Annotated[float, Strict(), Field(gt=0, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Strict(), Field(ge=0, allow_inf_nan=False)]
UnitFraction = Annotated[float, Strict(), Field(gt=0, lt=1, allow_inf_nan=False)]
PositiveInt = Annotated[int, Strict(), Field(gt=0)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://[^\s|]+$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Param[T](_Strict):
    """Valor con trazabilidad: estado y referencia a la fuente o decisión."""

    unsafe_value: T | None = Field(alias="value")
    status: ParamStatus
    source_ref: NonEmptyStr
    note: str = ""

    def require(self, name: str) -> T:
        """Devuelve el valor solo si es utilizable; si no, lanza ``UnverifiedValueError``."""
        if self.status not in USABLE_STATUSES:
            raise UnverifiedValueError(
                f"parámetro {name!r} con estado {self.status!r} no utilizable "
                f"(fuente: {self.source_ref})"
            )
        if self.unsafe_value is None:
            raise UnverifiedValueError(f"parámetro {name!r} {self.status} pero sin valor")
        return self.unsafe_value


# ------------------------------------------------------------------ sessions


class WeeklyTime(_Strict):
    weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    time: WallTime


class SessionsConfig(_Strict):
    """Sesión de negociación CL. Horas de pared en America/New_York."""

    timezone: Literal["America/New_York"]
    weekly_open_et: Param[WeeklyTime]
    daily_close_et: Param[WallTime]
    daily_reopen_et: Param[WallTime]
    settlement_time_et: Param[WallTime]


# ------------------------------------------------------------------ contracts


class ContractsConfig(_Strict):
    root_symbol: Literal["CL"]
    exchange: NonEmptyStr
    contract_size_bbl: Param[PositiveInt]
    tick_size_usd_per_bbl: Param[PositiveFloat]
    tick_value_usd: Param[PositiveFloat]
    last_trade_rule: Param[NonEmptyStr]
    roll_rule: Param[NonEmptyStr]


# ------------------------------------------------------------------ horizons


class HorizonSpec(_Strict):
    name: NonEmptyStr
    kind: Literal["market_minutes", "session_end", "sessions"]
    length: PositiveInt | None = None

    @model_validator(mode="after")
    def _length_matches_kind(self) -> Self:
        if (self.kind == "session_end") != (self.length is None):
            raise ValueError(
                f"horizonte {self.name!r}: 'length' solo y siempre si kind != session_end"
            )
        return self


def _unique_horizon_names(v: tuple[HorizonSpec, ...]) -> tuple[HorizonSpec, ...]:
    names = [h.name for h in v]
    if len(names) != len(set(names)):
        raise ValueError(f"nombres de horizonte duplicados: {names}")
    return v


class HorizonsConfig(_Strict):
    horizons: Param[Annotated[tuple[HorizonSpec, ...], AfterValidator(_unique_horizon_names)]]
    time_basis: Param[Literal["market_time", "clock_time"]]
    session_end_reference: Param[Literal["settlement", "globex_close"]]


# ------------------------------------------------------------------ costs


class CostsConfig(_Strict):
    currency: Literal["USD"]
    commission_usd_per_side: Param[NonNegativeFloat]
    spread_ticks: Param[NonNegativeFloat]
    slippage_ticks_per_side: Param[NonNegativeFloat]
    latency_seconds: Param[NonNegativeFloat]
    sensitivity_multipliers: Param[tuple[PositiveFloat, ...]]


# ------------------------------------------------------------------ splits


class SplitsConfig(_Strict):
    train_fraction: Param[UnitFraction]
    validation_fraction: Param[UnitFraction]
    test_fraction: Param[UnitFraction]
    embargo: Param[NonEmptyStr]
    sealed_test_window: Param[NonEmptyStr]

    @model_validator(mode="after")
    def _fractions_sum_to_one(self) -> Self:
        parts = [self.train_fraction, self.validation_fraction, self.test_fraction]
        values = [p.unsafe_value for p in parts]
        if all(v is not None for v in values) and not math.isclose(
            sum(v for v in values if v is not None), 1.0, abs_tol=1e-9
        ):
            raise ValueError(f"las fracciones train/validation/test deben sumar 1: {values}")
        return self


# ------------------------------------------------------------------ sources

_SECRET_IN_URL_RE = re.compile(r"(api[_-]?key|token|secret|password)=", re.IGNORECASE)


class SourceSpec(_Strict):
    name: NonEmptyStr
    description: NonEmptyStr
    docs_url: HttpsUrl
    terms_url: HttpsUrl | None
    probe_url: HttpsUrl
    auth: Literal["none", "query_param", "basic_user"]
    auth_env: NonEmptyStr | None = None
    auth_param: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _auth_consistent(self) -> Self:
        if _SECRET_IN_URL_RE.search(self.probe_url):
            raise ValueError(f"{self.name}: probe_url no puede contener credenciales")
        needs_env = self.auth != "none"
        needs_param = self.auth == "query_param"
        if needs_env != (self.auth_env is not None):
            raise ValueError(f"{self.name}: auth_env requerido solo si auth != none")
        if needs_param != (self.auth_param is not None):
            raise ValueError(f"{self.name}: auth_param requerido solo si auth == query_param")
        return self


class SourcesConfig(_Strict):
    sources: tuple[SourceSpec, ...]

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [s.name for s in self.sources]
        if len(names) != len(set(names)):
            raise ValueError(f"nombres de fuente duplicados: {names}")
        return self


# ------------------------------------------------------------------ gdelt


def _polars_regex(v: str) -> str:
    """Comprueba que el patrón compila en el motor de Polars (regex de Rust)."""
    import polars as pl  # noqa: PLC0415 - import diferido: solo al validar

    try:
        pl.Series([""]).str.contains(v)
    except pl.exceptions.ComputeError as exc:
        raise ValueError(f"regex inválida para Polars: {v!r}") from exc
    return v


PolarsRegex = Annotated[NonEmptyStr, AfterValidator(_polars_regex)]
ThemeToken = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]+$")]
ThemePrefix = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_]+$")]


class GkgFilterConfig(_Strict):
    """Filtro amplio y versionado del GKG (ADR-011). Cambiarlo exige nueva ``version``."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    theme_tokens: Annotated[tuple[ThemeToken, ...], Field(min_length=1)]
    theme_exclude_prefixes: tuple[ThemePrefix, ...]
    title_regex: PolarsRegex
    org_regex: PolarsRegex
    url_regex: PolarsRegex
    reject_relevance_regex: PolarsRegex
    # Columnas GKG que no se guardan (se validan contra el esquema GKG al usarlas).
    drop_columns: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _unique_tokens(self) -> Self:
        if len(set(self.theme_tokens)) != len(self.theme_tokens):
            raise ValueError("theme_tokens duplicados")
        return self


class GkgIngestConfig(_Strict):
    url_prefix: HttpsUrl
    max_reject_fraction_per_day: UnitFraction
    max_reject_fraction_per_file: UnitFraction
    reject_records_tolerance_per_file: Annotated[int, Strict(), Field(ge=0)]
    max_missing_404_per_day: Annotated[int, Strict(), Field(ge=0)]
    max_malformed_index_lines: Annotated[int, Strict(), Field(ge=0)]
    http_timeout_s: PositiveFloat
    retry_backoff_s: tuple[PositiveFloat, ...]
    download_threads: PositiveInt
    workers: PositiveInt
    min_free_disk_gb: PositiveFloat


class GdeltConfig(_Strict):
    gkg_filter: GkgFilterConfig
    ingest: GkgIngestConfig


# ------------------------------------------------------------------ capture


class DarwinexInput(_Strict):
    rel_path: NonEmptyStr
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    parent_rel_path: NonEmptyStr


class DarwinexConfig(_Strict):
    """Capa normalizada Darwinex XTIUSD M1 (``config/darwinex.yaml``)."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    input: DarwinexInput
    server_offset_vs_new_york_hours: Param[Annotated[int, Strict(), Field(ge=-12, le=14)]]
    bar_timestamp_is_start: Param[Annotated[bool, Strict()]]
    daily_break_start_et: Param[WallTime]
    daily_break_end_et: Param[WallTime]
    max_drop_fraction: Param[UnitFraction]
    plausible_server_range: Param[tuple[date, date]]


def load_darwinex_config(config_dir: Path = CONFIG_DIR) -> DarwinexConfig:
    """Carga ``config/darwinex.yaml``."""
    return load_yaml_model(config_dir / "darwinex.yaml", DarwinexConfig)


class WpsrInputs(_Strict):
    archive_index: NonEmptyStr
    fred_release_dates: NonEmptyStr
    schedule: NonEmptyStr
    archive_pages_glob: NonEmptyStr


class WpsrRedefinition(_Strict):
    series_id: NonEmptyStr
    before_week_ending: date


class EiaWpsrConfig(_Strict):
    """Calendario y valores WPSR point-in-time (``config/eia_wpsr.yaml``)."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    inputs: WpsrInputs
    series: dict[NonEmptyStr, NonEmptyStr] = Field(min_length=1)
    first_release_date: Param[date]
    min_lag_days: Param[PositiveInt]
    max_lag_days: Param[PositiveInt]
    known_shared_releases: Param[list[date]]
    standard_release_time_et: Param[WallTime]
    rpit_time_et: Param[WallTime]
    redefinition: Param[WpsrRedefinition]


def load_eia_wpsr_config(config_dir: Path = CONFIG_DIR) -> EiaWpsrConfig:
    """Carga ``config/eia_wpsr.yaml``."""
    return load_yaml_model(config_dir / "eia_wpsr.yaml", EiaWpsrConfig)


class CotInputs(_Strict):
    zips_glob: NonEmptyStr
    socrata_systemfields: NonEmptyStr
    statute: NonEmptyStr
    closure_eos: NonEmptyStr


class CftcCotConfig(_Strict):
    """COT point-in-time (``config/cftc_cot.yaml``)."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    contract_code: Annotated[str, StringConstraints(pattern=r"^[0-9A-Z]{6}$")]
    inputs: CotInputs
    first_as_of: Param[date]
    release_business_day: Param[PositiveInt]
    official_release_time_et: Param[WallTime]
    rpit_time_et: Param[WallTime]
    api_first_as_of: Param[date]
    api_created_at_margin_minutes: Param[Annotated[int, Strict(), Field(ge=0, le=240)]]
    verified_releases: Param[dict[date, date]]
    catch_up_window: Param[tuple[date, date]]
    catch_up_expected_releases: Param[dict[date, date]]


def load_cftc_cot_config(config_dir: Path = CONFIG_DIR) -> CftcCotConfig:
    """Carga ``config/cftc_cot.yaml``."""
    return load_yaml_model(config_dir / "cftc_cot.yaml", CftcCotConfig)


class FredCalendarInputs(_Strict):
    statute: NonEmptyStr
    closure_eos: NonEmptyStr


class FredConfig(_Strict):
    """Vintages ALFRED de series diarias (``config/fred.yaml``)."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    series_glob: NonEmptyStr
    calendar_inputs: FredCalendarInputs
    series: Param[list[NonEmptyStr]]
    available_rule: Param[Literal["next_day_midnight_et"]]
    release_schedule: Param[dict[NonEmptyStr, Literal["h15", "h10", "none"]]]
    official_release_time_et: Param[WallTime]
    first_vintage: Param[dict[NonEmptyStr, date]]


def load_fred_config(config_dir: Path = CONFIG_DIR) -> FredConfig:
    """Carga ``config/fred.yaml``."""
    return load_yaml_model(config_dir / "fred.yaml", FredConfig)


class NewsSource(_Strict):
    filter_version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    start: date
    end: date  # excluido

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError(f"rango vacío {self.start} → {self.end}")
        return self


class NewsConfig(_Strict):
    """Noticias normalizadas (``config/news.yaml``)."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    sources: list[NewsSource] = Field(min_length=1)
    availability_lag_minutes: Param[PositiveInt]
    published_ahead_tolerance_hours: Param[PositiveInt]
    published_min_year: Param[Annotated[int, Strict(), Field(ge=1900, le=2100)]]

    @model_validator(mode="after")
    def _no_overlap(self) -> Self:
        spans = sorted((s.start, s.end) for s in self.sources)
        for (_, end), (start, _) in itertools.pairwise(spans):
            if start < end:
                raise ValueError("rangos de fuentes solapados")
        return self


def load_news_config(config_dir: Path = CONFIG_DIR) -> NewsConfig:
    """Carga ``config/news.yaml``."""
    return load_yaml_model(config_dir / "news.yaml", NewsConfig)


RuleTarget = Literal["relevance", "family", "mechanism"]


class EventRule(_Strict):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    value: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    field: Literal["title", "url", "themes", "organizations"]
    regex: NonEmptyStr
    only_without_title: bool = False


class EventRulesConfig(_Strict):
    """Reglas de relevancia, familia y mecanismo (``config/events_rules.yaml``)."""

    version: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    defaults: dict[RuleTarget, Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]]
    relevance: list[EventRule] = Field(min_length=1)
    family: list[EventRule] = Field(min_length=1)
    mechanism: list[EventRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [r.name for rules in (self.relevance, self.family, self.mechanism) for r in rules]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"nombres de regla repetidos: {dup}")
        if set(self.defaults) != {"relevance", "family", "mechanism"}:
            raise ValueError("defaults debe definir relevance, family y mechanism")
        return self


def load_event_rules_config(config_dir: Path = CONFIG_DIR) -> EventRulesConfig:
    """Carga ``config/events_rules.yaml``."""
    return load_yaml_model(config_dir / "events_rules.yaml", EventRulesConfig)


class CaptureConfig(_Strict):
    """Captura prospectiva (D12, ADR-016)."""

    gdelt_lastupdate_url: HttpsUrl
    gdelt_file_prefix: HttpsUrl
    gdelt_slot_minutes: PositiveInt
    gdelt_pending_expiry_hours: PositiveInt
    eia_table1_url: HttpsUrl
    cftc_url: HttpsUrl
    cftc_contract_code: Annotated[str, StringConstraints(pattern=r"^\d{6}$")]
    user_agent: NonEmptyStr
    http_timeout_s: PositiveFloat
    max_seen: PositiveInt


def load_capture_config(config_dir: Path = CONFIG_DIR) -> CaptureConfig:
    """Carga ``config/capture.yaml``."""
    return load_yaml_model(config_dir / "capture.yaml", CaptureConfig)


# ------------------------------------------------------------------ project


class ProjectConfig(_Strict):
    sessions: SessionsConfig
    contracts: ContractsConfig
    horizons: HorizonsConfig
    costs: CostsConfig
    splits: SplitsConfig
    sources: SourcesConfig


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader que rechaza claves duplicadas (PyYAML se queda con la última)."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    keys = [loader.construct_object(k) for k, _ in node.value]
    dupes = sorted({str(k) for k in keys if keys.count(k) > 1})
    if dupes:
        raise SchemaError(f"claves YAML duplicadas {dupes} en línea {node.start_mark.line + 1}")
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_yaml_model[M: BaseModel](path: Path, model: type[M]) -> M:
    """Carga ``path`` y lo valida contra ``model``. Errores → ``SchemaError``."""
    if not path.is_file():
        raise SchemaError(f"el fichero de configuración {path} no existe")
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path.name}: YAML inválido\n{exc}") from exc
    if not isinstance(raw, dict):
        raise SchemaError(f"{path.name}: se esperaba un mapping YAML en la raíz")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"{path.name}: configuración inválida\n{exc}") from exc


def load_gdelt_config(config_dir: Path = CONFIG_DIR) -> GdeltConfig:
    """Carga ``config/gdelt.yaml``."""
    return load_yaml_model(config_dir / "gdelt.yaml", GdeltConfig)


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
