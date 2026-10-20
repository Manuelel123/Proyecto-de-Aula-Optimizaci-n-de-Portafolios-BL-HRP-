"""``flask users ...``: maintenance commands for local accounts."""

import click

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.users import get_user_by_email, normalize_email, reassign_owner
from optimizacion_portafolios.web.auth import bp
from optimizacion_portafolios.web.auth.roles import GESTOR_ROLE, current_gestors, grant_gestor, revoke_gestor


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


def _account(email: str):
    user = get_user_by_email(email)
    if user is None:
        raise click.ClickException(
            f"No existe una cuenta local con el correo {normalize_email(email)}. "
            "Regístrala primero en /auth/registro."
        )
    return user


@bp.cli.command("make-gestor")
@click.argument("email")
@click.option("--replace", is_flag=True, help="Reemplaza a la cuenta gestor actual (vuelve a novato).")
def make_gestor(email: str, replace: bool):
    """Otorga el rol gestor (acceso total) a la cuenta de EMAIL.

    Es la única forma de obtener ese rol: no se puede elegir desde la web.
    Solo puede haber una cuenta gestor.
    """
    user = _account(email)
    try:
        demoted = grant_gestor(user, replace=replace)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    db.session.commit()
    for other in demoted:
        click.echo(f"{other.email} dejó de ser gestor (ahora es novato).")
    click.echo(f"{user.email} es ahora la cuenta gestor.")


@bp.cli.command("revoke-gestor")
@click.argument("email")
def revoke_gestor_command(email: str):
    """Quita el rol gestor a la cuenta de EMAIL (vuelve a novato)."""
    user = _account(email)
    if user.role != GESTOR_ROLE:
        raise click.ClickException(f"{user.email} no es gestor.")
    revoke_gestor(user)
    db.session.commit()
    click.echo(f"{user.email} ya no es gestor.")


@bp.cli.command("list-gestor")
def list_gestor():
    """Muestra qué cuenta tiene el rol gestor."""
    gestors = current_gestors()
    click.echo(", ".join(user.email for user in gestors) if gestors else "No hay cuenta gestor.")
