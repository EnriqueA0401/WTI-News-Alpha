"""Utilidades de tiempo point-in-time.

Invariantes:
- Todo timestamp interno es tz-aware y se almacena en UTC.
- America/New_York solo se usa para lógica de sesión/calendario.
- Un timestamp naive lanza ``NaiveTimestampError``.
- Una hora de pared inexistente (hueco del salto DST de marzo) lanza siempre
  ``CalendarError``. Una hora ambigua (vuelta DST de noviembre) lanza
  ``CalendarError`` salvo que el llamador declare ``ambiguous="use_fold"``:
  resolverla en silencio podría adelantar una hora un ``available_at``.
"""

from datetime import UTC, date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

import polars as pl

from wti_news_alpha.errors import CalendarError, NaiveTimestampError, SchemaError

NEW_YORK = ZoneInfo("America/New_York")

AmbiguousPolicy = Literal["raise", "use_fold"]


def _transition_kind(ts: datetime) -> Literal["gap", "ambiguous"] | None:
    """Clasifica la hora de pared de ``ts`` en su zona (PEP 495).

    En un hueco, ``fold=0`` usa el offset anterior (menor) y ``fold=1`` el
    posterior; en una hora repetida ocurre lo contrario.
    """
    off0 = ts.replace(fold=0).utcoffset()
    off1 = ts.replace(fold=1).utcoffset()
    if off0 is None or off1 is None or off0 == off1:
        return None
    return "gap" if off0 < off1 else "ambiguous"


def _require_valid_aware(ts: datetime, ambiguous: AmbiguousPolicy) -> None:
    if not isinstance(ts, datetime):
        raise CalendarError(f"se esperaba datetime, se recibió {type(ts).__name__}")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise NaiveTimestampError(f"timestamp naive no permitido: {ts!r}")
    kind = _transition_kind(ts)
    if kind == "gap":
        raise CalendarError(f"la hora {ts!r} no existe en su zona horaria (salto DST)")
    if kind == "ambiguous" and ambiguous == "raise":
        raise CalendarError(
            f"la hora {ts!r} es ambigua en su zona horaria (vuelta DST); "
            "usa ambiguous='use_fold' solo si fold proviene de una conversión fiable"
        )


def ensure_utc(ts: datetime, *, ambiguous: AmbiguousPolicy = "raise") -> datetime:
    """Devuelve el mismo instante en UTC.

    Entrada: datetime tz-aware en cualquier zona. Salida: datetime con tzinfo=UTC.
    """
    _require_valid_aware(ts, ambiguous)
    return ts.astimezone(UTC)


def to_new_york(ts: datetime, *, ambiguous: AmbiguousPolicy = "raise") -> datetime:
    """Devuelve el mismo instante expresado en America/New_York.

    Entrada: datetime tz-aware. Salida: datetime con tzinfo=America/New_York
    (``fold`` correcto en la hora repetida).
    """
    _require_valid_aware(ts, ambiguous)
    return ts.astimezone(NEW_YORK)


def ny_wall_to_utc(d: date, t: time) -> datetime:
    """Convierte una hora de pared de Nueva York (fecha + hora) a UTC.

    Se usa para horas publicadas por fuentes oficiales (p. ej. EIA 10:30 ET).
    Lanza ``CalendarError`` si la hora no existe o es ambigua por DST, si ``t``
    trae zona horaria o si ``d`` es un datetime (su hora/zona se perderían).
    """
    if isinstance(d, datetime):
        raise CalendarError(f"se esperaba date, no datetime: {d!r}")
    if t.tzinfo is not None:
        raise CalendarError(f"la hora de pared no debe traer tzinfo: {t!r}")
    local = datetime.combine(d, t, tzinfo=NEW_YORK)
    kind = _transition_kind(local)
    if kind == "gap":
        raise CalendarError(f"la hora {d} {t} no existe en America/New_York (salto DST)")
    if kind == "ambiguous":
        raise CalendarError(f"la hora {d} {t} es ambigua en America/New_York (vuelta DST)")
    return local.astimezone(UTC)


def require_utc_column(df: pl.DataFrame, column: str, *, allow_nulls: bool = False) -> None:
    """Valida que ``column`` exista, sea ``Datetime`` en UTC y (por defecto) sin nulos.

    Lanza ``SchemaError`` (columna ausente, tipo o zona incorrectos, nulos) o
    ``NaiveTimestampError`` (Datetime sin zona).
    """
    if column not in df.columns:
        raise SchemaError(f"la columna {column!r} no existe")
    dtype = df.schema[column]
    if not isinstance(dtype, pl.Datetime):
        raise SchemaError(f"la columna {column!r} debe ser Datetime, es {dtype}")
    if dtype.time_zone is None:
        raise NaiveTimestampError(f"la columna {column!r} es Datetime naive")
    if dtype.time_zone != "UTC":
        raise SchemaError(f"la columna {column!r} debe estar en UTC, está en {dtype.time_zone}")
    n_null = df.get_column(column).null_count()
    if n_null and not allow_nulls:
        raise SchemaError(f"la columna {column!r} tiene {n_null} nulos")
