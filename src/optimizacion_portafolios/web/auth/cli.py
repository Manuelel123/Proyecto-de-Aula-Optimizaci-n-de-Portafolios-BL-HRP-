"""``flask users ...``: maintenance commands for local accounts."""

import click

from optimizacion_portafolios.storage.users import get_user_by_email, normalize_email, reassign_owner
from optimizacion_portafolios.web.auth import bp


@bp.cli.command("reassign-portfolios")
@click.argument("old_user_id")
@click.argument("email")
def reassign_portfolios(old_user_id: str, email: str):
    """Pasa los portafolios (y backtests) de OLD_USER_ID a la cuenta local de EMAIL.

    Sirve para migrar los datos guardados con el id de usuario de InsForge.
    """
    user = get_user_by_email(email)
    if user is None:
        raise click.ClickException(
            f"No existe una cuenta local con el correo {normalize_email(email)}. "
            "Regístrala primero en /auth/registro."
        )
    if old_user_id == str(user.id):
        raise click.ClickException("El id anterior ya es el de esa cuenta.")
    counts = reassign_owner(old_user_id, user)
    for table, count in counts.items():
        click.echo(f"{table}: {count} filas reasignadas a {user.email} (id {user.id})")
