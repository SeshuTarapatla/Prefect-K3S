__all__ = ["prefect_k3s"]

from importlib.metadata import version
from pathlib import Path
from subprocess import run
from sys import version_info

from my_modules.git import Git
from my_modules.logger import get_logger
from my_modules.postgres import Postgres, PostgresSecret
from my_modules.datetime import now
from sqlalchemy import text
from typer import Typer

from prefect_k3s.vars import PREFECT_DATABASE, PREFECT_IMAGE

prefect_k3s = Typer(
    name="prefect_k3s",
    help="Prefect K3S Command-line utility.",
    no_args_is_help=True,
    add_completion=False,
)

log = get_logger(__name__)


@prefect_k3s.command(
    name="init", help="Initialize required setup before start including db creation."
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
def build(prefix: str = PREFECT_IMAGE):
    started_at = now()
    python_version = f"{version_info.major}.{version_info.minor}"
    prefect_version = version("prefect")
    tag = f"{prefect_version}-python{python_version}"
    base_image = f"prefecthq/prefect:{tag}"
    custom_image = f"{prefix}:{tag}"
    sqlalchemy_conn_url = PostgresSecret.get_connection_string(
        PREFECT_DATABASE, local=False, engine="asyncpg"
    )
    git = Git()

    log.info(f"Current python version: {python_version}")
    log.info(f"Prefect version installed: {prefect_version}")
    log.info(f"Base Image: '{base_image}'")
    log.info(f"Building custom image with dependencies injected: '{custom_image}'")

    dockerfile = Path("Dockerfile")
    dockefile_contents = "\n".join(
        (
            f"FROM {base_image}",
            "",
            f"ENV SQLALCHEMY_CONN_URL={sqlalchemy_conn_url}",
            f"RUN uv pip install git+{git.remote_url}@{git.current_branch}"
        )
    )
    dockerfile.write_text(dockefile_contents)
    run(["docker", "build", "--no-cache", "-t", custom_image, "."])
    log.info(f"Build complete. Time taken: {now() - started_at}")
