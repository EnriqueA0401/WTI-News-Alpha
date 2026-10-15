"""Utilidades de tiempo point-in-time.

Invariantes:
- Todo timestamp interno es tz-aware y se almacena en UTC.
- America/New_York solo se usa para lógica de sesión/calendario.
- Un timestamp naive lanza ``NaiveTimestampError``; una hora de pared
  inexistente (salto DST de marzo) o ambigua (vuelta de noviembre) lanza
  ``CalendarError`` en lugar de resolverse en silencio.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import polars as pl

from wti_news_alpha.errors import CalendarError, NaiveTimestampError, SchemaError

NEW_YORK = ZoneInfo("America/New_York")


def _require_aware(ts: datetime) -> None:
    """Exige tz-aware y que la hora de pared exista en su zona.

    Una hora en el hueco del salto DST (p. ej. 02:30 America/New_York el
    8-mar-2026) no corresponde a ningún instante real: se rechaza. Una hora
    ambigua es válida porque ``fold`` la desambigua.
    """
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise NaiveTimestampError(f"timestamp naive no permitido: {ts!r}")
    roundtrip = ts.astimezone(UTC).astimezone(ts.tzinfo)
    if roundtrip.replace(tzinfo=None, fold=0) != ts.replace(tzinfo=None, fold=0):
        raise CalendarError(f"la hora {ts!r} no existe en su zona horaria (salto DST)")


def ensure_utc(ts: datetime) -> datetime:
    """Devuelve el mismo instante en UTC.

    Entrada: datetime tz-aware en cualquier zona. Salida: datetime con tzinfo=UTC.
    """
    _require_aware(ts)
    return ts.astimezone(UTC)


def to_new_york(ts: datetime) -> datetime:
    """Devuelve el mismo instante expresado en America/New_York.

    Entrada: datetime tz-aware. Salida: datetime con tzinfo=America/New_York.
    """
    _require_aware(ts)
    return ts.astimezone(NEW_YORK)


def ny_wall_to_utc(d: date, t: time) -> datetime:
    """Convierte una hora de pared de Nueva York (fecha + hora) a UTC.

    Se usa para horas publicadas por fuentes oficiales (p. ej. EIA 10:30 ET).
    Lanza ``CalendarError`` si la hora no existe o es ambigua por DST, o si
    ``t`` ya trae zona horaria.
    """
    if t.tzinfo is not None:
        raise CalendarError(f"la hora de pared no debe traer tzinfo: {t!r}")
    first = datetime.combine(d, t, tzinfo=NEW_YORK).replace(fold=0)
    second = first.replace(fold=1)
    if first.utcoffset() != second.utcoffset():
        # zoneinfo asigna offsets distintos a fold=0/1 tanto en la hora
        # inexistente como en la ambigua; se distinguen con un round-trip.
        roundtrip = first.astimezone(UTC).astimezone(NEW_YORK)
        if roundtrip.replace(tzinfo=None) != first.replace(tzinfo=None):
            raise CalendarError(f"la hora {d} {t} no existe en America/New_York (salto DST)")
        raise CalendarError(f"la hora {d} {t} es ambigua en America/New_York (vuelta DST)")
    return first.astimezone(UTC)


def require_utc_column(df: pl.DataFrame, column: str) -> None:
    """Valida que ``column`` exista y sea ``Datetime`` con zona UTC.

    Lanza ``SchemaError`` (columna ausente, tipo o zona incorrectos) o
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
