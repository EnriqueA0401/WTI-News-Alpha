"""Excepciones propias del proyecto.

Política anti-fallos-silenciosos (CLAUDE.md §6.3): los invariantes de runtime se
comprueban explícitamente y fallan con una de estas excepciones, nunca con
``assert`` (se desactiva con ``python -O``).
"""


class WNAError(Exception):
    """Base de todas las excepciones de WTI News Alpha."""


class LeakageError(WNAError):
    """Un dato con ``available_at > decision_time`` intentó entrar en un cálculo."""


class SchemaError(WNAError):
    """Un DataFrame o configuración no cumple el esquema esperado."""


class CalendarError(WNAError):
    """Error de tiempo, zona horaria, sesión o calendario."""


class NaiveTimestampError(CalendarError):
    """Se recibió un timestamp sin zona horaria."""


class RolloverError(WNAError):
    """Un retorno o etiqueta cruza contratos sin regla de roll documentada."""


class SealedTestError(WNAError):
    """Intento de acceso al periodo de test sellado sin pre-registro y flag explícito."""


class SourceAccessError(WNAError):
    """Una fuente de datos no es accesible (red, bloqueo, credencial ausente)."""


class UnverifiedValueError(WNAError):
    """Se intentó usar un parámetro de configuración no verificado ni aprobado."""


class PhaseNotImplementedError(WNAError):
    """Comando reservado para una fase posterior del plan (CLAUDE.md §12)."""


class RawIntegrityError(WNAError):
    """Un fichero de ``data/raw`` no coincide con su manifiesto o se intentó sobrescribir."""
