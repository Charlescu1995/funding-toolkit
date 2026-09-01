"""
Paso 3 — normalización de intervalos + APR anualizado.

Uso:
    python cli.py             # datos en vivo (necesita internet real hacia los exchanges)
    python cli.py --offline   # datos grabados, para probar sin depender de la red
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict

from rich.console import Console
from rich.table import Table

from connectors.base import FundingRate, VenueType
from core.aggregate import build_matrix, exchange_columns
from core.history import WINDOWS_HOURS, historical_apr_all_windows, init_db
from core.normalize import NormalizedRate, normalize_all
from core.opportunities import compute_opportunities

logging.basicConfig(level=logging.WARNING)
console = Console()


def build_connectors(offline: bool) -> list:
    if offline:
        from connectors.offline import binance_offline, bybit_offline, hyperliquid_offline

        return [binance_offline(), bybit_offline(), hyperliquid_offline()]

    from connectors.cex_ccxt import binance, bybit
    from connectors.dex_hyperliquid import HyperliquidConnector

    return [binance(), bybit(), HyperliquidConnector()]


def fetch_all(connectors: list) -> list[FundingRate]:
    all_rates: list[FundingRate] = []
    for conn in connectors:
        rates = conn.fetch_funding_rates()
        console.print(f"  [dim]{conn.name:<14}[/dim] -> {len(rates)} pares")
        all_rates.extend(rates)
    return all_rates


def render_normalized_table(rates: list[NormalizedRate]) -> None:
    table = Table(title="Funding rates normalizados (APR anualizado, comparable entre exchanges)")
    table.add_column("Exchange", style="bold")
    table.add_column("Tipo")
    table.add_column("Símbolo")
    table.add_column("Raw (intervalo nativo)", justify="right")
    table.add_column("Intervalo", justify="right")
    table.add_column("Cada 8h (equiv.)", justify="right")
    table.add_column("APR anualizado", justify="right")

    for r in sorted(rates, key=lambda r: r.apr_pct, reverse=True):
        venue_style = "cyan" if r.venue_type == VenueType.CEX else "magenta"
        apr_style = "green" if r.apr_pct >= 0 else "red"
        table.add_row(
            r.exchange,
            f"[{venue_style}]{r.venue_type.value}[/{venue_style}]",
            r.symbol,
            f"{r.raw_rate * 100:+.4f}%",
            f"{r.interval_hours:g}h",
            f"{r.rate_per_8h_pct:+.4f}%",
            f"[{apr_style}]{r.apr_pct:+.2f}%[/{apr_style}]",
        )

    console.print(table)


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "[dim]s/d[/dim]"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    return f"${value:,.0f}"


def render_opportunities(rates: list[NormalizedRate]) -> None:
    """
    Paso 5: para cada símbolo presente en 2+ exchanges, calcula el mejor par
    long/short y lo enriquece con Consistency Score (¿se ha mantenido este
    spread en el tiempo, o es ruido de un instante?) y OI Depth (¿hay
    profundidad de verdad en las dos piernas, o una de las dos es papel fino?).

    Esto ya es prácticamente la vista de ranking final (Paso 6 solo le añade
    la vista de matriz alternativa y pulido de presentación).
    """
    conn = init_db()
    opportunities = compute_opportunities(rates, conn)

    table = Table(title="Oportunidades con Consistency Score y OI Depth (Paso 5)")
    table.add_column("Símbolo", style="bold")
    table.add_column("LONG en (recibe)", justify="left")
    table.add_column("SHORT en (recibe)", justify="left")
    table.add_column("Spread APR (ahora)", justify="right")
    table.add_column("Consistency\n(30d)", justify="right")
    table.add_column("OI long", justify="right")
    table.add_column("OI short", justify="right")
    table.add_column("Cuello de botella", justify="right")

    for opp in opportunities:
        if opp.consistency_pct is None:
            cons_cell = f"[dim]s/d ({opp.consistency_samples} obs.)[/dim]"
        else:
            cons_style = "green" if opp.consistency_pct >= 70 else ("yellow" if opp.consistency_pct >= 50 else "red")
            cons_cell = f"[{cons_style}]{opp.consistency_pct:.0f}%[/{cons_style}]"

        bottleneck_cell = _fmt_usd(opp.oi_bottleneck_usd)
        if opp.oi_bottleneck_side:
            bottleneck_cell += f" [dim]({opp.oi_bottleneck_side})[/dim]"

        table.add_row(
            opp.symbol,
            f"{opp.long_exchange} ({opp.long_apr:+.1f}%)",
            f"{opp.short_exchange} ({opp.short_apr:+.1f}%)",
            f"[bold green]{opp.spread_apr:.1f}%[/bold green]",
            cons_cell,
            _fmt_usd(opp.oi_long_usd),
            _fmt_usd(opp.oi_short_usd),
            bottleneck_cell,
        )

    conn.close()
    console.print(table)
    console.print(
        "[dim]Consistency: % del tiempo (30d) que esta asignación long/short habría sido rentable. "
        "s/d = sin histórico suficiente todavía.[/dim]"
    )


def render_matrix(rates: list[NormalizedRate]) -> None:
    """
    Paso 6 — vista matriz, estilo Loris: cada símbolo contra cada exchange,
    en una sola rejilla, sin que nadie pre-filtre "las mejores" por ti.

    Marca en verde la celda más baja de la fila (mejor sitio para ir LONG,
    ahí te pagan) y en rojo la más alta (mejor sitio para ir SHORT), igual
    que el BUY/SELL de Loris.
    """
    matrix = build_matrix(rates)
    columns = exchange_columns(rates)

    table = Table(title="Matriz completa: símbolo × exchange (APR anualizado)")
    table.add_column("Símbolo", style="bold")
    for ex in columns:
        table.add_column(ex, justify="right")

    for symbol in sorted(matrix):
        row_rates = matrix[symbol]
        values = {ex: r.apr_pct for ex, r in row_rates.items()}
        best_long_ex = min(values, key=values.get) if len(values) >= 2 else None
        best_short_ex = max(values, key=values.get) if len(values) >= 2 else None

        cells = [symbol]
        for ex in columns:
            if ex not in row_rates:
                cells.append("[dim]—[/dim]")
                continue
            apr = row_rates[ex].apr_pct
            text = f"{apr:+.1f}%"
            if ex == best_long_ex:
                cells.append(f"[bold green]{text}[/bold green] [dim]BUY[/dim]")
            elif ex == best_short_ex:
                cells.append(f"[bold red]{text}[/bold red] [dim]SELL[/dim]")
            else:
                cells.append(text)
        table.add_row(*cells)

    console.print(table)
    console.print("[dim]BUY = mejor exchange para ir long (te pagan más). SELL = mejor exchange para ir short.[/dim]")


def render_historical_table(rates: list[NormalizedRate]) -> None:
    """
    Paso 4: en vez de solo el APR de este instante, la media REAL de snapshots
    guardados en cada ventana (1h/24h/7d/30d) — estilo John5Cripto.

    Requiere que ya haya snapshots guardados en data/funding_history.db
    (`python tools/seed_demo_history.py` para probarlo con datos de demo).
    """
    conn = init_db()
    table = Table(title="APR histórico REAL por ventana (medias de snapshots, no la tasa instantánea)")
    table.add_column("Exchange", style="bold")
    table.add_column("Símbolo")
    for label in WINDOWS_HOURS:
        table.add_column(label.upper(), justify="right")

    # Evitamos filas duplicadas exchange+símbolo si vinieran repetidas
    seen = set()
    for r in rates:
        key = (r.exchange, r.symbol)
        if key in seen:
            continue
        seen.add(key)

        windows = historical_apr_all_windows(conn, r.exchange, r.symbol)
        cells = []
        for label in WINDOWS_HOURS:
            stat = windows[label]
            if not stat.enough_history or stat.apr_avg is None:
                cells.append("[dim]s/d[/dim]")  # "sin datos suficientes", equivalente al guion de John5Cripto
            else:
                style = "green" if stat.apr_avg >= 0 else "red"
                cells.append(f"[{style}]{stat.apr_avg:+.1f}%[/{style}]")

        table.add_row(r.exchange, r.symbol, *cells)

    conn.close()
    console.print(table)
    console.print("[dim]s/d = sin histórico suficiente todavía en esa ventana[/dim]")


def filter_rates(
    rates: list[NormalizedRate], venue: str, exchanges: list[str] | None
) -> list[NormalizedRate]:
    out = rates
    if venue != "all":
        wanted = VenueType.CEX if venue == "cex" else VenueType.DEX
        out = [r for r in out if r.venue_type == wanted]
    if exchanges:
        wanted_ex = {e.strip().lower() for e in exchanges}
        out = [r for r in out if r.exchange.lower() in wanted_ex]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Funding Toolkit — Paso 6: ranking + matriz")
    parser.add_argument("--offline", action="store_true", help="usar fixtures grabados en vez de red real")
    parser.add_argument("--history", action="store_true", help="mostrar también el APR histórico real (Paso 4)")
    parser.add_argument("--matrix", action="store_true", help="mostrar la vista matriz (Paso 6) en vez del ranking")
    parser.add_argument("--venue", choices=["all", "cex", "dex"], default="all", help="filtrar por tipo de venue")
    parser.add_argument("--exchanges", type=str, default=None,
                         help="lista separada por comas, ej. binanceusdm,hyperliquid")
    args = parser.parse_args()

    console.print(f"\n[bold]Funding Toolkit[/bold] — modo: {'offline (fixtures)' if args.offline else 'en vivo'}\n")

    connectors = build_connectors(args.offline)
    console.print("Consultando exchanges:")
    raw_rates = fetch_all(connectors)

    if not raw_rates:
        console.print("\n[red]No se obtuvo ningún dato.[/red] Si estás en modo en vivo, revisa tu conexión "
                       "a internet hacia los exchanges (este sandbox de desarrollo no la tiene).")
        return

    normalized = normalize_all(raw_rates)
    exchanges_list = args.exchanges.split(",") if args.exchanges else None
    normalized = filter_rates(normalized, args.venue, exchanges_list)

    if not normalized:
        console.print("\n[red]El filtro no dejó ningún resultado.[/red] Revisa --venue / --exchanges.")
        return

    console.print()
    render_normalized_table(normalized)
    console.print()

    if args.matrix:
        render_matrix(normalized)
    else:
        render_opportunities(normalized)

    if args.history:
        console.print()
        render_historical_table(normalized)

    console.print(f"\n[dim]Total: {len(normalized)} funding rates tras filtros, de {len(connectors)} exchanges consultados.[/dim]")


if __name__ == "__main__":
    main()
