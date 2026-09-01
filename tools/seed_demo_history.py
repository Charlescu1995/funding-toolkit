"""
Genera 30 días de snapshots sintéticos (cada 15 min) en la base de datos local,
partiendo de los mismos fixtures offline que usa `cli.py --offline`.

Esto es SOLO para poder enseñar cómo se ve el histórico real (Paso 4) sin
tener que esperar 30 días de verdad. En producción, esta tabla se rellena
sola: un scheduler llama a `record_snapshot()` cada 15 min con datos reales
(ver core/history.py).

Uso:
    python tools/seed_demo_history.py
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors.offline import binance_offline, bybit_offline, hyperliquid_offline
from core.history import DEFAULT_DB_PATH, SNAPSHOT_INTERVAL_MIN, init_db, record_snapshot
from core.normalize import normalize_all

DAYS_OF_HISTORY = 30
random.seed(42)  # reproducible: mismos datos de demo cada vez que se corre


def main() -> None:
    if DEFAULT_DB_PATH.exists():
        DEFAULT_DB_PATH.unlink()

    conn = init_db()

    connectors = [binance_offline(), bybit_offline(), hyperliquid_offline()]
    current_rates = normalize_all([r for c in connectors for r in c.fetch_funding_rates()])

    now = datetime.now(timezone.utc)
    total_steps = int((DAYS_OF_HISTORY * 24 * 60) / SNAPSHOT_INTERVAL_MIN)

    print(f"Generando {total_steps} snapshots sintéticos por par "
          f"({DAYS_OF_HISTORY} días, cada {SNAPSHOT_INTERVAL_MIN} min)...")

    for step in range(total_steps, 0, -1):
        captured_at = now - timedelta(minutes=step * SNAPSHOT_INTERVAL_MIN)

        # Simulamos que la tasa "actual" es el punto final de un paseo aleatorio
        # con algo de mean-reversion, para que el histórico no sea una línea plana
        # ni un ruido sin sentido. No pretende ser realista, solo servir de demo.
        snapshot = []
        for r in current_rates:
            drift = random.gauss(0, abs(r.apr_pct) * 0.06 + 0.5)
            noisy_apr = r.apr_pct + drift
            snapshot.append(
                type(r)(
                    exchange=r.exchange,
                    venue_type=r.venue_type,
                    symbol=r.symbol,
                    raw_rate=r.raw_rate,
                    interval_hours=r.interval_hours,
                    apr_pct=noisy_apr,
                    rate_per_8h_pct=r.rate_per_8h_pct,
                    mark_price=r.mark_price,
                    open_interest_usd=r.open_interest_usd,
                )
            )

        record_snapshot(conn, snapshot, captured_at=captured_at)

    # y el snapshot "actual" de verdad, al final
    record_snapshot(conn, current_rates, captured_at=now)

    conn.close()
    print(f"Listo. Base de datos de demo en: {DEFAULT_DB_PATH}")


if __name__ == "__main__":
    main()
