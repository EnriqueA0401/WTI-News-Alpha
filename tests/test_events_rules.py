"""Tests de la clasificación por reglas (FASE_03.md paso 2-3). Titulares SINTÉTICOS."""

import polars as pl
import pytest
from pydantic import ValidationError

from wti_news_alpha.config import CONFIG_DIR, EventRulesConfig, load_event_rules_config
from wti_news_alpha.errors import SchemaError
from wti_news_alpha.events import rules as er

CFG = load_event_rules_config(CONFIG_DIR)


def _one(
    title: str | None, url: str = "https://x.com/a", themes: str = "", orgs: str = ""
) -> dict[str, str]:
    df = pl.DataFrame(
        {"title": [title], "url": [url], "themes": [themes], "organizations": [orgs]},
        schema={"title": pl.Utf8, "url": pl.Utf8, "themes": pl.Utf8, "organizations": pl.Utf8},
    )
    return er.apply_rules(df, CFG).row(0, named=True)


@pytest.mark.parametrize(
    ("title", "relevance", "family", "mechanism"),
    [
        (
            "OPEC+ agrees to deepen output cuts through 2026",
            "direct",
            "opec_policy",
            "tightens_supply",
        ),
        ("US crude inventories draw sharply, EIA says", "direct", "eia_inventories", "ambiguous"),
        (
            "Crude stocks fall for third week as exports climb",
            "direct",
            "eia_inventories",
            "tightens_supply",
        ),
        ("Drone attack hits Saudi oil facility", "direct", "conflict_attack", "tightens_supply"),
        (
            "Hurricane forces Gulf of Mexico oil platforms to shut",
            "direct",
            "weather",
            "tightens_supply",
        ),
        ("Oil prices rise as China demand rebounds", "direct", "demand_macro", "raises_demand"),
        ("Saudi Arabia to boost oil output in July", "direct", "other", "loosens_supply"),
        ("Exxon quarterly profits beat estimates", "not_relevant", "company", "ambiguous"),
        ("Brent crude edges higher in quiet trade", "direct", "market_report", "ambiguous"),
        ("Local bakery opens new store downtown", "not_relevant", "other", "ambiguous"),
        ("Two crude bombs explode outside court", "not_relevant", "other", "ambiguous"),
    ],
)
def test_hand_cases(title: str, relevance: str, family: str, mechanism: str) -> None:
    r = _one(title)
    assert (r["relevance"], r["family"], r["mechanism"]) == (relevance, family, mechanism)


def test_themes_and_orgs_without_title() -> None:
    r = _one(None, themes="TAX_FNCACT;ECON_OILPRICE;LEADER")
    assert (r["relevance"], r["relevance_rule"]) == ("direct", "theme_oilprice")
    assert (r["family"], r["family_rule"]) == ("other", "default")
    r = _one(None, orgs="Organization Of The Petroleum Exporting Countries")
    assert r["relevance_rule"] == "org_opec"
    r = _one(None, themes="ENV_OIL")
    assert r["relevance"] == "indirect"


def test_theme_only_decides_without_title() -> None:
    r = _one("As Ukraine war drags on, what does Putin want?", themes="ECON_OILPRICE")
    assert (r["relevance"], r["relevance_rule"]) == ("indirect", "theme_oil_context")


def test_opinion_by_url() -> None:
    r = _one("Why the Fed should not cut rates", url="https://news.com/opinion/columns/x")
    assert r["family"] == "rates_usd"  # el título gana por orden de reglas
    r = _one("Thoughts on the coming winter", url="https://news.com/opinion/x")
    assert (r["family"], r["family_rule"]) == ("opinion", "opinion_url")


def test_missing_columns_raise() -> None:
    with pytest.raises(SchemaError, match="faltan"):
        er.apply_rules(pl.DataFrame({"title": ["x"]}), CFG)


def test_regexes_compile_and_bad_regex_raises() -> None:
    er.check_regexes(CFG)
    data = CFG.model_dump()
    data["family"][0]["regex"] = "(unclosed"
    with pytest.raises(SchemaError, match="regex inválida"):
        er.check_regexes(EventRulesConfig.model_validate(data))


def test_config_validation() -> None:
    data = CFG.model_dump()
    data["family"][1]["name"] = data["family"][0]["name"]
    with pytest.raises(ValidationError, match="repetidos"):
        EventRulesConfig.model_validate(data)
    data = CFG.model_dump()
    del data["defaults"]["mechanism"]
    with pytest.raises(ValidationError, match="defaults"):
        EventRulesConfig.model_validate(data)
