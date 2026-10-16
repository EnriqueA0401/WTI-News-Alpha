"""Clasificación por reglas: relevancia WTI, familia de evento y mecanismo (fase 1 del §6.3).

Invariantes:
- Cada artículo recibe, por objetivo, el valor de la **primera** regla que
  coincide (orden de ``config/events_rules.yaml``) y el nombre de esa regla
  (trazabilidad); si ninguna coincide o el campo es nulo, el valor por defecto
  y la regla ``default``.
- La clasificación de un artículo solo depende de sus propios campos: no usa
  información de otros artículos ni del futuro.
"""

import polars as pl

from wti_news_alpha.config import EventRule, EventRulesConfig, RuleTarget
from wti_news_alpha.errors import SchemaError

TARGETS: tuple[RuleTarget, ...] = ("relevance", "family", "mechanism")


def _match(rule: EventRule) -> pl.Expr:
    m = pl.col(rule.field).str.contains(f"(?i){rule.regex}").fill_null(False)  # noqa: FBT003
    return m & pl.col("title").is_null() if rule.only_without_title else m


def check_regexes(cfg: EventRulesConfig) -> None:
    """Compila cada regex con el motor de polars; una regex inválida → ``SchemaError``."""
    probe = pl.DataFrame({f: ["x"] for f in ("title", "url", "themes", "organizations")})
    for target in TARGETS:
        for rule in getattr(cfg, target):
            try:
                probe.select(_match(rule))
            except pl.exceptions.PolarsError as exc:
                raise SchemaError(f"regla {rule.name}: regex inválida ({exc})") from exc


def apply_rules(df: pl.DataFrame, cfg: EventRulesConfig) -> pl.DataFrame:
    """Añade ``<objetivo>`` y ``<objetivo>_rule`` para relevancia, familia y mecanismo."""
    missing = {"title", "url", "themes", "organizations"} - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas para las reglas: {sorted(missing)}")
    exprs = []
    for target in TARGETS:
        rules: list[EventRule] = getattr(cfg, target)
        value: pl.Expr = pl.lit(cfg.defaults[target])
        name: pl.Expr = pl.lit("default")
        for rule in reversed(rules):
            value = pl.when(_match(rule)).then(pl.lit(rule.value)).otherwise(value)
            name = pl.when(_match(rule)).then(pl.lit(rule.name)).otherwise(name)
        exprs += [value.alias(target), name.alias(f"{target}_rule")]
    return df.with_columns(exprs)
