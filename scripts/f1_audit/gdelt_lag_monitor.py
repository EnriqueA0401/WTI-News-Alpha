"""F1: mide cuándo aparece cada fichero GDELT 2.0 de 15 min frente a su timestamp nominal.

Sondea https://data.gdeltproject.org/gdeltv2/lastupdate.txt cada ``--interval`` segundos
durante ``--hours`` horas y escribe una línea JSON por cada fichero nuevo observado:
nominal_utc (del nombre), observed_utc (primera vez visto), lag_s y la cota de
resolución (intervalo de sondeo). Uso: python gdelt_lag_monitor.py OUT.jsonl
"""

import argparse
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
TS_RE = re.compile(r"/(\d{14})\.export\.CSV\.zip")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--hours", type=float, default=8.0)
    a = ap.parse_args()
    seen: str | None = None
    prev_poll: datetime | None = None
    deadline = time.monotonic() + a.hours * 3600
    with httpx.Client(timeout=20) as client, a.out.open("a", encoding="utf-8") as fh:
        while time.monotonic() < deadline:
            now = datetime.now(tz=UTC)
            try:
                text = client.get(URL).raise_for_status().text
            except httpx.HTTPError as exc:
                fh.write(
                    json.dumps({"observed_utc": now.isoformat(), "error": type(exc).__name__})
                    + "\n"
                )
                fh.flush()
                time.sleep(a.interval)
                continue
            m = TS_RE.search(text)
            if m is None:
                fh.write(
                    json.dumps({"observed_utc": now.isoformat(), "error": "unparsable"}) + "\n"
                )
            elif m.group(1) != seen:
                nominal = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                fh.write(
                    json.dumps(
                        {
                            "nominal_utc": nominal.isoformat(),
                            "observed_utc": now.isoformat(),
                            "prev_poll_utc": prev_poll.isoformat() if prev_poll else None,
                            "lag_s": round((now - nominal).total_seconds(), 1),
                            "first_observation": seen is None,
                        }
                    )
                    + "\n"
                )
                seen = m.group(1)
            fh.flush()
            prev_poll = now
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
