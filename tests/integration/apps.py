"""Shared integration app factories for the database-backed MCP test matrix."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from advanced_alchemy.base import UUIDAuditBase
from advanced_alchemy.extensions.litestar import AsyncSessionConfig, SQLAlchemyAsyncConfig, SQLAlchemyPlugin, providers
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.litestar import FromDishka, LitestarProvider, inject, setup_dishka
from dishka.integrations.litestar import FromDishka as Inject  # Phase 2.7: annotation alias
from litestar import Controller, Litestar, get
from litestar.di import Provide
from sqlalchemy.orm import Mapped, mapped_column
from sqlspec import SQLSpec
from sqlspec.adapters.asyncpg import AsyncpgConfig, AsyncpgDriver
from sqlspec.adapters.duckdb import DuckDBConfig, DuckDBDriver
from sqlspec.extensions.litestar import SQLSpecPlugin

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.executor import ToolExecutionContext
from tests.integration._auth import build_mcp_auth_config, build_oauth_backend

AuthMode = Literal["none", "bearer"]

AA_TABLE_NAME = "mcp_test_aa_widget"
AA_DISHKA_TABLE_NAME = "mcp_test_aa_dishka_widget"
SQLSPEC_TABLE_NAME = "mcp_test_sqlspec_report"
SQLSPEC_DISHKA_TABLE_NAME = "mcp_test_sqlspec_dishka_report"
DUCKDB_TABLE_NAME = "mcp_test_duckdb_report"

POSTGRES_TEST_TABLES = (
    AA_TABLE_NAME,
    AA_DISHKA_TABLE_NAME,
    SQLSPEC_TABLE_NAME,
    SQLSPEC_DISHKA_TABLE_NAME,
)


def _unexpected_dependency_provider(name: str) -> Provide:
    def _provider() -> Any:
        msg = f"{name} must be provided by the MCP dependency provider"
        raise RuntimeError(msg)

    return Provide(_provider, sync_to_thread=False)


def _mcp_plugin(dependency_provider: Any, *, auth_mode: AuthMode = "none") -> LitestarMCP:
    config = MCPConfig(dependency_provider=dependency_provider)
    if auth_mode == "bearer":
        config.auth = build_mcp_auth_config()
    return LitestarMCP(config)


def _mcp_plugin_no_deps(*, auth_mode: AuthMode = "none") -> LitestarMCP:
    config = MCPConfig()
    if auth_mode == "bearer":
        config.auth = build_mcp_auth_config()
    return LitestarMCP(config)


def _auth_on_app_init(auth_mode: AuthMode) -> list[Any]:
    """Return the ``on_app_init`` list appropriate for ``auth_mode``."""
    if auth_mode == "bearer":
        backend = build_oauth_backend()
        return [backend.on_app_init]
    return []


class AlchemyWidget(UUIDAuditBase):
    """Advanced Alchemy model used by the plain Postgres integration suite."""

    __tablename__ = AA_TABLE_NAME

    name: Mapped[str] = mapped_column(unique=True, index=True)


class AlchemyDishkaWidget(UUIDAuditBase):
    """Advanced Alchemy model used by the Dishka-backed Postgres suite."""

    __tablename__ = AA_DISHKA_TABLE_NAME

    name: Mapped[str] = mapped_column(unique=True, index=True)


class AlchemyWidgetService(SQLAlchemyAsyncRepositoryService[AlchemyWidget]):
    """Service used by the plain Advanced Alchemy integration suite."""

    class Repo(SQLAlchemyAsyncRepository[AlchemyWidget]):
        model_type = AlchemyWidget

    repository_type = Repo
    match_fields = ["name"]


class AlchemyDishkaWidgetService(SQLAlchemyAsyncRepositoryService[AlchemyDishkaWidget]):
    """Service used by the Dishka-backed Advanced Alchemy integration suite."""

    class Repo(SQLAlchemyAsyncRepository[AlchemyDishkaWidget]):
        model_type = AlchemyDishkaWidget

    repository_type = Repo
    match_fields = ["name"]


@dataclass
class SQLSpecReportRow:
    """Typed SQLSpec row model for async Postgres-backed suites."""

    title: str
    source: str


@dataclass
class DuckDBReportRow:
    """Typed SQLSpec row model for the sync DuckDB suite."""

    title: str
    source: str


class SQLSpecReportService:
    """SQLSpec service wrapper for async Postgres-backed report suites."""

    __slots__ = ("driver", "insert_sql", "select_all_sql", "select_one_sql", "source")

    def __init__(self, driver: AsyncpgDriver, *, table_name: str, source: str) -> None:
        self.driver = driver
        self.insert_sql = f"INSERT INTO {table_name} (title, source) VALUES ($1, $2)"
        self.select_all_sql = f"SELECT title, source FROM {table_name} ORDER BY title"
        self.select_one_sql = f"SELECT title, source FROM {table_name} WHERE title = $1"
        self.source = source

    async def create_report(self, title: str) -> SQLSpecReportRow:
        await self.driver.execute(self.insert_sql, title, self.source)
        await self.driver.commit()
        return await self.driver.select_one(self.select_one_sql, title, schema_type=SQLSpecReportRow)

    async def list_reports(self) -> list[SQLSpecReportRow]:
        return list(await self.driver.select(self.select_all_sql, schema_type=SQLSpecReportRow))


class DuckDBReportService:
    """SQLSpec service wrapper for the sync DuckDB suite."""

    __slots__ = ("driver",)

    def __init__(self, driver: DuckDBDriver) -> None:
        self.driver = driver

    def create_report(self, title: str) -> DuckDBReportRow:
        self.driver.execute(f"INSERT INTO {DUCKDB_TABLE_NAME} (title, source) VALUES (?, ?)", title, "duckdb")
        row = self.driver.select_one(
            f"SELECT title, source FROM {DUCKDB_TABLE_NAME} WHERE title = ?",
            title,
            schema_type=DuckDBReportRow,
        )
        return row

    def list_reports(self) -> list[DuckDBReportRow]:
        return list(self.driver.select(f"SELECT title, source FROM {DUCKDB_TABLE_NAME} ORDER BY title", schema_type=DuckDBReportRow))


async def _prepare_sqlspec_asyncpg_table(sqlspec: SQLSpec, config: AsyncpgConfig, table_name: str) -> None:
    async with sqlspec.provide_session(config) as db_session:
        await db_session.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL
            )
            """
        )
        await db_session.commit()


def _prepare_sqlspec_duckdb_table(sqlspec: SQLSpec, config: DuckDBConfig) -> None:
    with sqlspec.provide_session(config) as db_session:
        db_session.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {DUCKDB_TABLE_NAME} (
                title TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL
            )
            """
        )


def build_advanced_alchemy_app(
    connection_string: str,
    *,
    auth_mode: AuthMode = "none",
) -> Litestar:
    """Build a Litestar app using Advanced Alchemy against Postgres.

    Args:
        connection_string: Async SQLAlchemy connection string for Postgres.
        auth_mode: Either ``"none"`` (the default, no auth) or ``"bearer"``
            to wire the shared OAuth2 bearer backend plus MCP auth config.
    """

    alchemy_config = SQLAlchemyAsyncConfig(
        connection_string=connection_string,
        create_all=True,
        before_send_handler="autocommit",
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )

    class AlchemyWidgetController(Controller):
        path = "/alchemy/widgets"
        dependencies = providers.create_service_dependencies(AlchemyWidgetService, "widget_service", config=alchemy_config)

        @get("/", opt={"mcp_tool": "aa_create_widget"})
        async def create_widget(self, name: str, widget_service: AlchemyWidgetService) -> dict[str, str]:
            widget = await widget_service.create({"name": name}, auto_commit=True)
            return {"id": str(widget.id), "name": widget.name}

        @get("/resource", opt={"mcp_resource": "aa_widget_snapshot"})
        async def list_widgets(self, widget_service: AlchemyWidgetService) -> list[dict[str, str]]:
            widgets = await widget_service.list()
            return [{"id": str(widget.id), "name": widget.name} for widget in widgets]

    return Litestar(
        route_handlers=[AlchemyWidgetController],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), _mcp_plugin_no_deps(auth_mode=auth_mode)],
        on_app_init=_auth_on_app_init(auth_mode),
    )


def build_sqlspec_asyncpg_app(
    database_dsn: str,
    *,
    auth_mode: AuthMode = "none",
) -> Litestar:
    """Build a Litestar app using SQLSpec AsyncPG against Postgres."""

    sqlspec = SQLSpec()
    config = sqlspec.add_config(
        AsyncpgConfig(
            connection_config={"dsn": database_dsn},
            extension_config={"litestar": {"commit_mode": "autocommit"}},
        )
    )

    @asynccontextmanager
    async def dependency_provider(_context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        async with sqlspec.provide_session(config) as db_session:
            yield {
                "report_service": SQLSpecReportService(
                    db_session,
                    table_name=SQLSPEC_TABLE_NAME,
                    source="sqlspec",
                )
            }

    @get(
        "/sqlspec/reports",
        opt={"mcp_tool": "sqlspec_create_report"},
        dependencies={"report_service": _unexpected_dependency_provider("report_service")},
    )
    async def create_report(title: str, report_service: SQLSpecReportService) -> dict[str, str]:
        record = await report_service.create_report(title)
        return {"title": record.title, "source": record.source}

    async def on_startup() -> None:
        await _prepare_sqlspec_asyncpg_table(sqlspec, config, SQLSPEC_TABLE_NAME)

    return Litestar(
        route_handlers=[create_report],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), _mcp_plugin(dependency_provider, auth_mode=auth_mode)],
        on_app_init=_auth_on_app_init(auth_mode),
    )


class AdvancedAlchemyDishkaProvider(Provider):
    """Dishka providers for the Advanced Alchemy integration suite."""

    def __init__(self, alchemy_config: SQLAlchemyAsyncConfig) -> None:
        super().__init__()
        self.alchemy_config = alchemy_config

    @provide(scope=Scope.REQUEST)
    async def provide_db_session(self) -> AsyncIterator[Any]:
        async with self.alchemy_config.get_session() as db_session:
            yield db_session

    @provide(scope=Scope.REQUEST)
    def provide_widget_service(self, db_session: Any) -> AlchemyDishkaWidgetService:
        return AlchemyDishkaWidgetService(session=db_session)


def build_advanced_alchemy_dishka_app(
    connection_string: str,
    *,
    auth_mode: AuthMode = "none",
) -> Litestar:
    """Build a Litestar app using Advanced Alchemy and Dishka against Postgres."""

    alchemy_config = SQLAlchemyAsyncConfig(
        connection_string=connection_string,
        create_all=True,
        before_send_handler="autocommit",
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )
    container = make_async_container(LitestarProvider(), AdvancedAlchemyDishkaProvider(alchemy_config))

    @asynccontextmanager
    async def dependency_provider(_context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        async with container() as request_container:
            yield {"widget_service": await request_container.get(AlchemyDishkaWidgetService)}

    @get(
        "/alchemy/dishka/widgets",
        opt={"mcp_tool": "aa_dishka_create_widget"},
        dependencies={"widget_service": _unexpected_dependency_provider("widget_service")},
    )
    @inject
    async def create_widget(name: str, widget_service: Inject[AlchemyDishkaWidgetService]) -> dict[str, str]:
        # Uses the `FromDishka as Inject` alias (Phase 2.7) to prove the
        # plugin's ``__dishka_orig_func__`` unwrap is annotation-alias
        # insensitive.
        widget = await widget_service.create({"name": name}, auto_commit=True)
        return {"id": str(widget.id), "name": widget.name, "source": "dishka"}

    async def close_container() -> None:
        await container.close()

    app = Litestar(
        route_handlers=[create_widget],
        on_shutdown=[close_container],
        plugins=[SQLAlchemyPlugin(config=alchemy_config), _mcp_plugin(dependency_provider, auth_mode=auth_mode)],
        on_app_init=_auth_on_app_init(auth_mode),
    )
    setup_dishka(container, app)
    return app


class SQLSpecDishkaProvider(Provider):
    """Dishka providers for the SQLSpec integration suite."""

    def __init__(self, sqlspec: SQLSpec, config: AsyncpgConfig) -> None:
        super().__init__()
        self.sqlspec = sqlspec
        self.config = config

    @provide(scope=Scope.REQUEST)
    async def provide_db_session(self) -> AsyncIterator[Any]:
        async with self.sqlspec.provide_session(self.config) as db_session:
            yield db_session

    @provide(scope=Scope.REQUEST)
    def provide_report_service(self, db_session: Any) -> SQLSpecReportService:
        return SQLSpecReportService(db_session, table_name=SQLSPEC_DISHKA_TABLE_NAME, source="sqlspec-dishka")


def build_sqlspec_dishka_app(
    database_dsn: str,
    *,
    auth_mode: AuthMode = "none",
) -> Litestar:
    """Build a Litestar app using SQLSpec and Dishka against Postgres."""

    sqlspec = SQLSpec()
    config = sqlspec.add_config(
        AsyncpgConfig(
            connection_config={"dsn": database_dsn},
            extension_config={"litestar": {"commit_mode": "autocommit"}},
        )
    )
    container = make_async_container(LitestarProvider(), SQLSpecDishkaProvider(sqlspec, config))

    @asynccontextmanager
    async def dependency_provider(_context: ToolExecutionContext) -> AsyncIterator[dict[str, Any]]:
        async with container() as request_container:
            yield {"report_service": await request_container.get(SQLSpecReportService)}

    @get(
        "/sqlspec/dishka/reports",
        opt={"mcp_tool": "sqlspec_dishka_create_report"},
        dependencies={"report_service": _unexpected_dependency_provider("report_service")},
    )
    @inject
    async def create_report(title: str, report_service: FromDishka[SQLSpecReportService]) -> dict[str, str]:
        record = await report_service.create_report(title)
        return {"title": record.title, "source": record.source}

    async def on_startup() -> None:
        await _prepare_sqlspec_asyncpg_table(sqlspec, config, SQLSPEC_DISHKA_TABLE_NAME)

    async def close_container() -> None:
        await container.close()

    app = Litestar(
        route_handlers=[create_report],
        on_startup=[on_startup],
        on_shutdown=[close_container],
        plugins=[SQLSpecPlugin(sqlspec), _mcp_plugin(dependency_provider, auth_mode=auth_mode)],
        on_app_init=_auth_on_app_init(auth_mode),
    )
    setup_dishka(container, app)
    return app


def build_sqlspec_duckdb_app(
    database_path: str,
    *,
    auth_mode: AuthMode = "none",
) -> Litestar:
    """Build a Litestar app using SQLSpec's sync DuckDB adapter."""

    sqlspec = SQLSpec()
    config = sqlspec.add_config(
        DuckDBConfig(
            connection_config={"database": database_path},
            extension_config={"litestar": {"commit_mode": "autocommit"}},
        )
    )

    @contextmanager
    def dependency_provider(_context: ToolExecutionContext) -> Iterator[dict[str, Any]]:
        with sqlspec.provide_session(config) as db_session:
            yield {"report_service": DuckDBReportService(db_session)}

    @get(
        "/sqlspec/duckdb/reports",
        opt={"mcp_tool": "sqlspec_duckdb_create_report"},
        dependencies={"report_service": _unexpected_dependency_provider("report_service")},
        sync_to_thread=False,
    )
    def create_report(title: str, report_service: DuckDBReportService) -> dict[str, str]:
        record = report_service.create_report(title)
        return {"title": record.title, "source": record.source}

    def on_startup() -> None:
        _prepare_sqlspec_duckdb_table(sqlspec, config)

    return Litestar(
        route_handlers=[create_report],
        on_startup=[on_startup],
        plugins=[SQLSpecPlugin(sqlspec), _mcp_plugin(dependency_provider, auth_mode=auth_mode)],
        on_app_init=_auth_on_app_init(auth_mode),
    )
