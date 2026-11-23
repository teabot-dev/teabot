"""The plugin loader: what loads, and why the rest does not (D13, D14, D46)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from teabot.core.loader import Candidate, LoadReport, PluginStatus, discover, from_plugins, load
from teabot.core.processes import open_lock_connection
from teabot.db.lockkeys import PLUGIN_NS, plugin_key
from teabot.db.migrate import MigrationReport, PluginMigrations, migrate
from teabot.db.naming import Naming
from teabot.db.pool import Login
from teabot.db.types import Conn
from teabot.sdk import ActionSpec, JobContext, Manifest, Registrar, Risk


class In(BaseModel):
    pass


async def noop_run(_ctx: Any, _data: In) -> In:
    return In()


async def noop_job(_ctx: JobContext) -> None:
    return None


@dataclass
class FakePlugin:
    manifest: Manifest
    action_names: Sequence[str] = ()
    job_names: Sequence[str] = ()
    fail_setup: bool = False
    setups: list[int] = field(default_factory=list[int])

    def setup(self, registrar: Registrar) -> None:
        self.setups.append(1)
        for name in self.action_names:
            registrar.action(ActionSpec(name, "an action", In, In, Risk.READ, noop_run))
        for name in self.job_names:
            registrar.job(name, noop_job)
        if self.fail_setup:
            raise RuntimeError("setup broke")


def plugin(plugin_id: str, **kwargs: Any) -> FakePlugin:
    return FakePlugin(Manifest(plugin_id, "1.0"), **kwargs)


async def no_migrations(plugins: Sequence[PluginMigrations]) -> MigrationReport:
    return MigrationReport(applied={p.plugin_id: [] for p in plugins})


async def run(candidates: list[Candidate], **kwargs: Any) -> LoadReport:
    return await load(
        candidates,
        disabled=kwargs.pop("disabled", ()),
        migrate_with=kwargs.pop("migrate_with", no_migrations),
        **kwargs,
    )


async def test_plugins_load_with_their_actions_and_jobs() -> None:
    report = await run(from_plugins([plugin("alpha", action_names=["alpha.list"], job_names=["tick"])]))
    assert report.statuses["alpha"].loaded
    assert set(report.loaded["alpha"].actions) == {"alpha.list"}
    assert set(report.loaded["alpha"].jobs) == {"tick"}


async def test_a_disabled_plugin_is_blocked_and_never_set_up() -> None:
    alpha = plugin("alpha")
    report = await run(from_plugins([alpha, plugin("beta")]), disabled=["alpha"])
    assert report.statuses["alpha"].reason == "blocked" and not alpha.setups
    assert report.statuses["beta"].loaded


async def test_a_failing_setup_keeps_nothing_and_others_load() -> None:
    report = await run(
        from_plugins([plugin("alpha", action_names=["alpha.x"], fail_setup=True), plugin("beta")])
    )
    status = report.statuses["alpha"]
    assert status.reason == "failed" and "setup broke" in (status.error or "")
    assert "alpha" not in report.loaded and report.statuses["beta"].loaded


async def test_an_action_outside_the_plugins_namespace_fails_the_plugin() -> None:
    report = await run(from_plugins([plugin("alpha", action_names=["beta.steal"])]))
    assert report.statuses["alpha"].reason == "failed"
    assert "beta.steal" in (report.statuses["alpha"].error or "")


async def test_the_same_action_twice_fails_the_plugin() -> None:
    report = await run(from_plugins([plugin("alpha", action_names=["alpha.x", "alpha.x"])]))
    assert report.statuses["alpha"].reason == "failed"


async def test_a_duplicate_id_loads_neither_and_names_both() -> None:
    report = await run(from_plugins([plugin("alpha"), plugin("alpha"), plugin("beta")]))
    status = report.statuses["alpha"]
    assert status.reason == "duplicate_id" and "alpha" not in report.loaded
    assert (status.error or "").count("FakePlugin") == 2
    assert "teabot plugins purge alpha" in (status.error or "")
    assert report.statuses["beta"].loaded


async def test_a_failed_migration_fails_the_plugin() -> None:
    async def failing(plugins: Sequence[PluginMigrations]) -> MigrationReport:
        return MigrationReport(applied={"beta": []}, failed={"alpha": "boom"})

    report = await run(from_plugins([plugin("alpha"), plugin("beta")]), migrate_with=failing)
    assert report.statuses["alpha"].reason == "failed" and report.statuses["alpha"].error == "boom"
    assert report.statuses["beta"].loaded


@dataclass
class FakeDist:
    name: str


@dataclass
class FakeEntryPoint:
    name: str
    target: object
    dist: FakeDist

    def load(self) -> object:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target


def test_discovery_reports_import_failures_and_non_plugins() -> None:
    eps = [
        FakeEntryPoint("alpha", plugin("alpha"), FakeDist("teabot-alpha")),
        FakeEntryPoint("broken", ImportError("no module"), FakeDist("teabot-broken")),
        FakeEntryPoint("odd", object(), FakeDist("teabot-odd")),
    ]

    def find(group: str) -> list[FakeEntryPoint]:
        return eps if group == "teabot.plugins" else []

    candidates = {c.name: c for c in discover(find)}
    assert candidates["alpha"].plugin is not None and candidates["alpha"].origin == "teabot-alpha"
    assert candidates["broken"].plugin is None and "no module" in (candidates["broken"].error or "")
    assert candidates["odd"].plugin is None and "not a teabot plugin" in (candidates["odd"].error or "")


async def test_a_broken_entry_point_is_reported_failed() -> None:
    eps = [FakeEntryPoint("broken", ImportError("no module"), FakeDist("teabot-broken"))]

    def find(_group: str) -> list[FakeEntryPoint]:
        return eps

    report = await run(discover(find))
    assert report.statuses["broken"].reason == "failed"


async def test_loaded_plugins_hold_shared_locks_and_a_purge_blocks_loading(
    admin: Conn, owner_login: Login
) -> None:
    lock = await open_lock_connection(owner_login, lambda: None)
    assert await admin.fetchval("SELECT pg_try_advisory_lock($1, $2)", PLUGIN_NS, plugin_key("beta"))
    try:
        report = await run(from_plugins([plugin("alpha"), plugin("beta")]), lock=lock)
        assert report.statuses["alpha"].loaded
        assert report.statuses["beta"].reason == "failed" and "purge" in (report.statuses["beta"].error or "")
        assert (
            await admin.fetchval("SELECT pg_try_advisory_lock($1, $2)", PLUGIN_NS, plugin_key("alpha"))
            is False
        )
    finally:
        await lock.close()


async def test_migrations_run_for_the_plugins_that_load(
    admin: Conn, naming: Naming, pools: Any, tmp_path: Path
) -> None:
    directory = tmp_path / "alpha"
    directory.mkdir()
    (directory / "0001_t.sql").write_text("CREATE TABLE t (guild_id bigint);")
    alpha = FakePlugin(Manifest("alpha", "1.0", migrations=directory))

    async def real(plugins: Sequence[PluginMigrations]) -> MigrationReport:
        return await migrate(admin, naming, plugins)

    report = await run(from_plugins([alpha, plugin("beta")]), migrate_with=real, disabled=["beta"])
    assert report.statuses["alpha"].loaded
    assert await admin.fetchval("SELECT version FROM core.migrations WHERE plugin_id = 'alpha'") == 1
    assert not await admin.fetchval("SELECT 1 FROM pg_namespace WHERE nspname = 'plugin_beta'")


def test_reasons_are_never_set_for_loaded_plugins() -> None:
    with pytest.raises(ValueError):
        PluginStatus(loaded=True, reason="failed")
    with pytest.raises(ValueError):
        PluginStatus(loaded=False)


async def test_failures_are_logged_as_errors_and_blocks_are_not(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="teabot.core.loader")
    await run(from_plugins([plugin("alpha"), plugin("beta", fail_setup=True)]), disabled=["alpha"])
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.__dict__["plugin"] for r in errors] == ["beta"]


@dataclass
class ManifestOnly:
    manifest: Manifest


def test_an_object_without_setup_is_not_a_plugin() -> None:
    eps = [FakeEntryPoint("half", ManifestOnly(Manifest("half", "1.0")), FakeDist("teabot-half"))]

    def find(_group: str) -> list[FakeEntryPoint]:
        return eps

    (candidate,) = discover(find)
    assert candidate.plugin is None and "not a teabot plugin" in (candidate.error or "")


async def test_a_plugin_whose_setup_fails_releases_its_lock(admin: Conn, owner_login: Login) -> None:
    lock = await open_lock_connection(owner_login, lambda: None)
    try:
        report = await run(from_plugins([plugin("alpha", fail_setup=True)]), lock=lock)
        assert report.statuses["alpha"].reason == "failed"
        assert (
            await admin.fetchval("SELECT pg_try_advisory_lock($1, $2)", PLUGIN_NS, plugin_key("alpha"))
            is True
        )
    finally:
        await lock.close()


async def test_status_labels_are_loaded_or_the_reason() -> None:
    report = await run(from_plugins([plugin("alpha"), plugin("beta")]), disabled=["beta"])
    assert report.labels() == {"alpha": "loaded", "beta": "blocked"}
