"""
Paso 4 — snapshots históricos y APR "real".

El problema que resuelve esto (robado de John5Cripto): el APR anualizado que
calculamos en el Paso 3 es la tasa de ESTE instante, extrapolada a un año.
Un par puede enseñar +3.500% APR durante una hora y desplomarse justo después
— si decides entrar basándote solo en la tasa actual, puedes llegar tarde.

La solución: guardar un snapshot cada X minutos (recomendado ~15, igual que
John5Cripto) y, en vez de enseñar solo "la tasa de ahora", enseñar la MEDIA
REAL de esas tasas en varias ventanas: última hora, último día, última
semana, último mes. Eso es lo que de verdad te dice si un spread es
consistente o es ruido de un momento.

Quién llama a `record_snapshot()` y con qué frecuencia es cosa de la capa que
programe las llamadas (Paso de scheduler / cron, cuando despleguemos esto de
verdad) — este módulo solo se encarga de guardar y consultar.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.normalize import NormalizedRate

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "funding_history.db"

# Ventanas que enseñamos, en horas. Por debajo de este número de snapshots
# esperados en la ventana, la marcamos como "sin histórico suficiente" en vez
# de enseñar una media poco fiable calculada con 1 o 2 puntos.
WINDOWS_HOURS = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}
SNAPSHOT_INTERVAL_MIN = 15
MIN_SNAPSHOTS_FRACTION = 0.5  # con menos del 50% de los snapshots esperados, no es fiable


def init_db(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS funding_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            apr_pct REAL NOT NULL,
            raw_rate REAL NOT NULL,
            interval_hours REAL NOT NULL,
            captured_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_lookup "
        "ON funding_snapshots (exchange, symbol, captured_at)"
    )
    conn.commit()
    return conn


def record_snapshot(
    conn: sqlite3.Connection,
    rates: list[NormalizedRate],
    captured_at: datetime | None = None,
) -> int:
    """Guarda un snapshot de todas las tasas normalizadas actuales. Devuelve cuántas filas insertó."""
    ts = (captured_at or datetime.now(timezone.utc)).isoformat()
    rows = [(r.exchange, r.symbol, r.apr_pct, r.raw_rate, r.interval_hours, ts) for r in rates]
    conn.executemany(
        "INSERT INTO funding_snapshots (exchange, symbol, apr_pct, raw_rate, interval_hours, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


@dataclass
class WindowStat:
    apr_avg: float | None   # None si no hay histórico suficiente en esta ventana
    samples: int
    enough_history: bool


def historical_apr(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    window_hours: float,
    now: datetime | None = None,
) -> WindowStat:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=window_hours)).isoformat()

    cur = conn.execute(
        "SELECT AVG(apr_pct), COUNT(*) FROM funding_snapshots "
        "WHERE exchange = ? AND symbol = ? AND captured_at >= ?",
        (exchange, symbol, since),
    )
    avg, count = cur.fetchone()
    expected = max(1, int((window_hours * 60) / SNAPSHOT_INTERVAL_MIN))
    enough = count >= expected * MIN_SNAPSHOTS_FRACTION

    return WindowStat(apr_avg=avg if enough else avg, samples=count, enough_history=enough)


def historical_apr_all_windows(
    conn: sqlite3.Connection, exchange: str, symbol: str, now: datetime | None = None
) -> dict[str, WindowStat]:
    """Equivalente a las columnas 1H / 24H / 7D / 30D de John5Cripto."""
    return {
        label: historical_apr(conn, exchange, symbol, hours, now=now)
        for label, hours in WINDOWS_HOURS.items()
    }
