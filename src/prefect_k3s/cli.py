__all__ = ["prefect_k3s"]

import asyncio
from importlib.metadata import version
from pathlib import Path
from subprocess import run
from sys import version_info
from time import sleep

import httpx
from my_modules.datetime_utils import now
from my_modules.git import Git
from my_modules.logger import get_logger
from my_modules.postgres import Postgres, PostgresSecret
from sqlalchemy import text
from typer import Option, Typer

from prefect_k3s.config import PrefectConfig
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
def init(
    drop: bool = Option(
        False, "-d", "--drop", help="Drop any existing Prefect database and metadata."
    ),
):
    db = Postgres(PREFECT_DATABASE)
    if drop and db.exists:
        log.info(
            f"{PREFECT_DATABASE.capitalize()} database exists: [bold red]Dropping[/]..."
        )
        db.drop_db(force=True)
    if db.exists:
        log.info(
            f"[bold blue]{PREFECT_DATABASE}[/] PostgreSQL database already exists."
        )
    else:
        log.info(
            f"Creating a PostgreSQL database [bold blue]{PREFECT_DATABASE}[/] for prefect."
        )
        with db.engine_dev.connect() as conn:
            sql = text(f"CREATE DATABASE {PREFECT_DATABASE};")
            conn.execute(sql)
        log.info("Database created successfully.")
    PrefectConfig.windows_init()


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
    sqlalchemy_conn_url = PostgresSecret.get_connection_string(local=False)

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
            " ENV TZ=Asia/Kolkata",
            f"ENV SQLALCHEMY_CONN_URL={sqlalchemy_conn_url}",
            *PrefectConfig.docker_env(),
            f"RUN uv pip install git+{git.remote_url}@{git.current_branch}",
        )
    )
    dockerfile.write_text(dockefile_contents)
    run(["docker", "build", "--no-cache", "-t", custom_image, "."])
    log.info(f"Build complete. Time taken: {now() - started_at}")


@prefect_k3s.command(
    name="purge",
    help="Cancel and delete all flow runs that have not reached Completed state.",
)
def purge():
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterState,
        FlowRunFilterStateType,
    )
    from prefect.client.schemas.objects import StateType
    from prefect.states import Cancelling

    CANCEL_FIRST = {StateType.RUNNING, StateType.PAUSED}

    async def _purge() -> None:
        async with get_client() as client:
            flow_run_filter = FlowRunFilter(
                state=FlowRunFilterState(
                    type=FlowRunFilterStateType(
                        not_any_=[StateType.COMPLETED]
                    )
                )
            )

            all_runs = []
            offset = 0
            batch = 200
            while True:
                page = await client.read_flow_runs(
                    flow_run_filter=flow_run_filter, limit=batch, offset=offset
                )
                all_runs.extend(page)
                if len(page) < batch:
                    break
                offset += batch

            if not all_runs:
                log.info("No flow runs to clean up.")
                return

            log.info(f"Found [bold]{len(all_runs)}[/] flow run(s) to clean up.")

            for run in all_runs:
                if run.state and run.state.type in CANCEL_FIRST:
                    try:
                        await client.set_flow_run_state(
                            run.id, Cancelling(), force=True
                        )
                        log.info(
                            f"Cancelled: [cyan]{run.name}[/] ({run.id}) "
                            f"[{run.state.type.value}]"
                        )
                    except Exception as e:
                        log.warning(f"Could not cancel {run.name} ({run.id}): {e}")

            deleted = 0
            for run in all_runs:
                try:
                    await client.delete_flow_run(run.id)
                    state_label = run.state.type.value if run.state else "unknown"
                    log.info(
                        f"Deleted:   [cyan]{run.name}[/] ({run.id}) [{state_label}]"
                    )
                    deleted += 1
                except Exception as e:
                    log.warning(f"Could not delete {run.name} ({run.id}): {e}")

            log.info(f"Done. Deleted [bold green]{deleted}[/] flow run(s).")

    asyncio.run(_purge())


@prefect_k3s.command(
    name="reset-pool",
    help="Clear stale worker registrations from the work pool without deleting scheduled runs.",
)
def reset_pool():
    from os import environ

    pool = environ.get("PREFECT_DEFAULT_WORK_POOL_NAME", "default-pool")
    api_url = PrefectConfig.PREFECT_API_URL_LOCAL()

    try:
        resp = httpx.get(f"{api_url}/work_pools/{pool}/workers", timeout=10)
        workers = resp.json().get("results", [])
        for w in workers:
            name = w["name"]
            httpx.delete(f"{api_url}/work_pools/{pool}/workers/{name}", timeout=10)
            log.info(f"Removed stale worker: {name}")
        log.info(
            f"Cleared {len(workers)} stale worker(s) from '{pool}'."
            if workers
            else f"No stale workers found in '{pool}'."
        )
    except Exception as e:
        log.warning(f"Could not clear stale workers: {e}")

    run(["prefect", "work-pool", "create", pool, "--type", "process"], capture_output=True)
    log.info(f"Work pool '{pool}' ready.")


@prefect_k3s.command(
    name="wait", help="Wait for Prefect server readiness and liveness."
)
def wait(
    timeout: int = Option(
        300, "-t", "--timeout", help="Timeout for wait (in seconds)."
    ),
):
    started_at = now()
    while (now() - started_at).total_seconds() <= timeout:
        health_endpoint = PrefectConfig.PREFECT_API_URL_LOCAL() + "/health"
        try:
            if httpx.get(health_endpoint).status_code == 200:
                log.info("Prefect server initialized and running.")
                return
            else:
                log.info("Prefect server initializing...")
                sleep(3)
        except Exception:
            log.info("Prefect server initializing...")
            sleep(3)
    raise TimeoutError("Timeout reached for server wait.")
