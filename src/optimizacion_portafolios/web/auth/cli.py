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
    Solo puede haber una cuenta gestor, y su correo debe estar verificado.
    """
    user = _account(email)
    if not user.email_verified:
        raise click.ClickException(
            f"{user.email} aún no verificó su correo: inicia sesión con esa cuenta y "
            "escribe el código que llega por correo antes de darle el rol gestor."
        )
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


def _insforge_client():
    from optimizacion_portafolios.insforge import InsForgeClient, InsForgeSettings

    settings = InsForgeSettings.from_env()
    if settings is None:
        raise click.ClickException("Faltan INSFORGE_URL e INSFORGE_API_KEY en .env.")
    return InsForgeClient(settings)


@bp.cli.command("import-insforge-backtests")
@click.option("--fallback", "fallback_email", help="Cuenta local que recibe los backtests cuyo dueño no tiene cuenta.")
@click.option("--dry-run", is_flag=True, help="Muestra qué se copiaría sin guardar nada.")
def import_insforge_backtests(fallback_email: str | None, dry_run: bool):
    """Trae el historial de backtests guardado en InsForge a las cuentas locales.

    Cada backtest va a la cuenta local con el mismo correo que su dueño en
    InsForge; los que esa cuenta ya tiene (mismos parámetros) se omiten.
    """
    from optimizacion_portafolios.insforge import InsForgeError
    from optimizacion_portafolios.storage.insforge_import import import_backtests

    try:
        report = import_backtests(_insforge_client(), fallback_email=fallback_email, dry_run=dry_run)
    except (InsForgeError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    prefix = "[simulación] " if dry_run else ""
    for email, count in report.imported.items():
        click.echo(f"{prefix}{email}: {count} backtests copiados")
    click.echo(f"{prefix}{report.results} resultados por estrategia; {report.duplicates} ya existían.")
    for owner, count in report.unmatched.items():
        click.echo(f"{owner}: {count} backtests omitidos (no tiene cuenta local; usa --fallback o regístrala).")
