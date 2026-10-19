"""``flask portfolios snapshot``: daily snapshot of every active portfolio."""

from datetime import date

import click

from optimizacion_portafolios.web.portfolios import bp, services


@bp.cli.command("snapshot")
@click.option(
    "--date", "as_of", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
    help="Valorar hasta esta fecha (AAAA-MM-DD); por defecto, hoy.",
)
def snapshot(as_of):
    """Guarda la foto diaria (valor, pesos y métricas) de los portafolios activos."""
    today = as_of.date() if as_of else date.today()
    outcome = services.snapshot_all(today)
    if not outcome:
        click.echo("No hay portafolios activos.")
        return
    failures = 0
    for portfolio, result in outcome:
        if isinstance(result, int):
            click.echo(f"#{portfolio.id} {portfolio.name}: {result} fotos guardadas")
        else:
            failures += 1
            click.echo(f"#{portfolio.id} {portfolio.name}: ERROR {result}", err=True)
    if failures:
        raise SystemExit(1)
