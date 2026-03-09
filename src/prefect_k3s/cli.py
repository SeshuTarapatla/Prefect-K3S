__all__ = ["prefect_k3s"]

from my_modules.logger import get_logger
from my_modules.postgres import Postgres
from sqlalchemy import text
from typer import Typer

from prefect_k3s.vars import PREFECT_DATABASE

prefect_k3s = Typer(
    name="prefect_k3s",
    help="Prefect K3S Command-line utility.",
    no_args_is_help=True,
    add_completion=False,
)

log = get_logger(__name__)


@prefect_k3s.command(
    name="init", help="Initialize required setup before start. Like DB creation."
)
def init():
    pg = Postgres(PREFECT_DATABASE)
    if pg.db_exists:
        log.info(f"[cyan]{PREFECT_DATABASE}[/] PostgreSQL database already exists.")
    else:
        log.info(
            f"Creating a PostgreSQL database [cyan]{PREFECT_DATABASE}[/] for prefect."
        )
        with pg.engine_dev.connect() as conn:
            sql = text(f"CREATE DATABASE {PREFECT_DATABASE};")
            conn.execute(sql)
        log.info("Database created successfully.")

@prefect_k3s.command(
    name="build",
    help="Docker build the custom prefect-k3s image with dependencies injected.",
)
def build(): ...
