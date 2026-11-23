"""The plugin loader (D4, D13, D14, D46).

Candidates come from `teabot.plugins` entry points, or from a list in tests. A plugin is loaded only when its
id is unique, it is not disabled, no purge holds its lock, its migrations apply and its `setup` succeeds. Every
other plugin gets a status with its reason, and everything else still loads.
"""

import importlib
import logging
import pkgutil
import sys
from collections import defaultdict
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from importlib.metadata import entry_points
from typing import Any, Literal, Protocol

from teabot.db.migrate import MigrationReport, PluginMigrations
from teabot.sdk import ActionSpec, JobHandler, LoadablePlugin
from teabot.sdk.plugin import EventName

log = logging.getLogger(__name__)

GROUP = "teabot.plugins"

type Reason = Literal["blocked", "failed", "duplicate_id", "uninstalled"]


@dataclass(frozen=True)
class PluginStatus:
    loaded: bool
    reason: Reason | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.loaded == (self.reason is not None):
            raise ValueError("a loaded plugin has no reason, and a plugin that is not loaded has one")


LOADED = PluginStatus(loaded=True)


@dataclass(frozen=True)
class Candidate:
    """One plugin offered to the loader. `plugin` is None when it could not even be imported."""

    name: str
    origin: str
    plugin: LoadablePlugin | None
    error: str | None = None


@dataclass
class Loaded:
    plugin: LoadablePlugin
    actions: dict[str, ActionSpec[Any, Any]] = field(default_factory=dict[str, ActionSpec[Any, Any]])
    jobs: dict[str, JobHandler] = field(default_factory=dict[str, JobHandler])
    events: frozenset[EventName] = frozenset()
    recurring: dict[str, timedelta] = field(default_factory=dict[str, timedelta])
    timeouts: dict[str, timedelta] = field(default_factory=dict[str, timedelta])
    # The member events it also hears when the member directory found them late (D55).
    late: frozenset[EventName] = frozenset()
    # The plugin events it subscribes to, by full name, each handled by its job `on_event:<name>`.
    subscriptions: frozenset[str] = frozenset()


@dataclass
class LoadReport:
    loaded: dict[str, Loaded] = field(default_factory=dict[str, Loaded])
    statuses: dict[str, PluginStatus] = field(default_factory=dict[str, PluginStatus])

    def labels(self) -> dict[str, str]:
        """Each plugin's status as `/healthz` and the process row show it: `loaded`, or the reason."""
        return {p: "loaded" if s.loaded else str(s.reason) for p, s in self.statuses.items()}

    def fail(self, plugin_id: str, reason: Reason, error: str | None = None) -> None:
        self.statuses[plugin_id] = PluginStatus(loaded=False, reason=reason, error=error)
        if reason != "blocked":
            log.error("plugin not loaded", extra={"plugin": plugin_id, "reason": reason, "error": error})


class EntryPointLike(Protocol):
    """An entry point. Its `dist`, when there is one, names the package that declared it."""

    @property
    def name(self) -> str: ...

    def load(self) -> Any: ...


class PluginLocks(Protocol):
    async def take_plugins(self, plugin_ids: Iterable[str]) -> list[str]: ...

    async def release_plugin(self, plugin_id: str) -> None: ...


type Migrator = Callable[[Sequence[PluginMigrations]], Awaitable[MigrationReport]]


def _is_plugin(obj: object) -> bool:
    return hasattr(obj, "manifest") and callable(getattr(obj, "setup", None))


def _candidate(ep: EntryPointLike) -> Candidate:
    dist = getattr(ep, "dist", None)
    origin: str = dist.name if dist is not None else ep.name
    module = str(getattr(ep, "module", "") or "")
    try:
        obj = ep.load()
        if hasattr(obj, "locate"):
            _import_package(module)
            obj.locate(module, getattr(dist, "version", None))
    except Exception as exc:
        return Candidate(ep.name, origin, None, f"import failed: {exc}")
    if not _is_plugin(obj):
        return Candidate(ep.name, origin, None, f"{ep.name} is not a teabot plugin")
    plugin_id = obj.manifest.id
    if plugin_id != ep.name:
        reason = (
            f"its entry point is named {ep.name} but the plugin's id is {plugin_id}; rename one so they match"
        )
        return Candidate(ep.name, origin, None, reason)
    return Candidate(plugin_id, origin, obj)


SKIPPED_MODULES = ("tests", "test", "__main__")
# A blocked plugin's candidate, never imported.
BLOCKED = "blocked by TEABOT_DISABLED_PLUGINS, so not imported"


def _import_package(module: str) -> None:
    """Import every module of a plugin's package, so actions and jobs decorated anywhere in it register. Tests,
    private modules and `__main__` are skipped."""
    package = sys.modules.get(module)
    path = getattr(package, "__path__", None)
    if package is None or path is None:
        return
    # pkgutil.walk_packages imports a subpackage to look inside it, so the skipping is done here, before import.
    for found in pkgutil.iter_modules(path):
        if found.name.startswith("_") or found.name in SKIPPED_MODULES:
            continue
        name = f"{module}.{found.name}"
        importlib.import_module(name)
        if found.ispkg:
            _import_package(name)


type Finder = Callable[[str], Iterable[EntryPointLike]]


def installed(group: str) -> Iterable[EntryPointLike]:
    """The entry points of the installed packages in `group`."""
    return entry_points(group=group)


def discover(find: Finder = installed, blocked: Collection[str] = ()) -> list[Candidate]:
    """Every `teabot.plugins` entry point, loaded or with the reason it could not be. A plugin in `blocked` is
    not imported at all, so none of its code runs (D13); its entry point's name is its id."""
    return [
        Candidate(ep.name, ep.name, None, BLOCKED) if ep.name in blocked else _candidate(ep)
        for ep in find(GROUP)
    ]


def from_plugins(plugins: Iterable[LoadablePlugin]) -> list[Candidate]:
    """Candidates from plugin objects, for tests that skip packaging."""
    return [Candidate(p.manifest.id, f"{type(p).__module__}.{type(p).__qualname__}", p) for p in plugins]


def event_job_name(event: str) -> str:
    """The job a plugin's handler for the plugin event `event` runs as."""
    return f"{SUBSCRIBER_PREFIX}{event}"


SUBSCRIBER_PREFIX = "on_event:"


class _Registrar:
    """Collects one plugin's registrations, so a failed setup leaves nothing behind."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self.actions: dict[str, ActionSpec[Any, Any]] = {}
        self.jobs: dict[str, JobHandler] = {}
        self.events: set[EventName] = set()
        self.late: set[EventName] = set()
        self.every: dict[str, timedelta] = {}
        self.timeouts: dict[str, timedelta] = {}
        self.subscriptions: set[str] = set()

    def recurring(self, name: str, every: timedelta) -> None:
        if name not in self.jobs:
            raise ValueError(f"job {name} recurs but is not registered")
        self.every[name] = every

    def action(self, spec: ActionSpec[Any, Any]) -> None:
        if spec.plugin_id != self.plugin_id:
            raise ValueError(f"{self.plugin_id} registered {spec.name}, outside its own namespace")
        if spec.name in self.actions:
            raise ValueError(f"{spec.name} registered twice")
        self.actions[spec.name] = spec

    def job(self, name: str, handler: JobHandler, timeout: timedelta | None = None) -> None:
        if name in self.jobs:
            raise ValueError(f"job {name} registered twice")
        self.jobs[name] = handler
        if timeout is not None:
            self.timeouts[name] = timeout

    def subscribe(self, name: str, handler: JobHandler) -> None:
        if name in self.subscriptions:
            raise ValueError(f"event {name} subscribed twice")
        self.subscriptions.add(name)
        self.job(event_job_name(name), handler)

    def event(self, name: EventName, handler: JobHandler, *, late: bool = False) -> None:
        if name in self.events:
            raise ValueError(f"event {name} registered twice")
        self.events.add(name)
        if late:
            self.late.add(name)
        self.job(f"on_{name}", handler)


def _clash(plugin_id: str, group: Sequence[Candidate]) -> str:
    """Who declares a shared ID, and the fix, since neither plugin loads until one goes."""
    return (
        f"declared by {' and '.join(c.origin for c in group)}, so neither loads. Uninstall one, or give it another "
        f"ID. Data kept under {plugin_id} stays with the ID: if the plugin you keep isn't the one that wrote it, "
        f"run `teabot plugins purge {plugin_id}` first."
    )


def _unique(candidates: Sequence[Candidate], report: LoadReport) -> dict[str, LoadablePlugin]:
    """Plugins whose id only one candidate declares. Unimportable and duplicated ones are reported."""
    by_id: defaultdict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.error == BLOCKED:
            report.fail(candidate.name, "blocked")
        elif candidate.plugin is None:
            report.fail(candidate.name, "failed", candidate.error)
        else:
            by_id[candidate.name].append(candidate)
    unique: dict[str, LoadablePlugin] = {}
    for plugin_id, group in by_id.items():
        if len(group) > 1:
            report.fail(plugin_id, "duplicate_id", _clash(plugin_id, group))
        else:
            unique[plugin_id] = group[0].plugin  # pyright: ignore[reportArgumentType]
    return unique


async def _lock(plugins: dict[str, LoadablePlugin], locks: PluginLocks | None, report: LoadReport) -> None:
    if locks is None:
        return
    for plugin_id in await locks.take_plugins(plugins):
        report.fail(plugin_id, "failed", "a purge of this plugin is running")
        del plugins[plugin_id]


async def _migrate(plugins: dict[str, LoadablePlugin], migrator: Migrator, report: LoadReport) -> list[str]:
    result = await migrator([PluginMigrations(p, plugins[p].manifest.migrations) for p in plugins])
    for plugin_id, error in result.failed.items():
        report.fail(plugin_id, "failed", error)
        del plugins[plugin_id]
    return list(result.failed)


def _setup(plugin_id: str, plugin: LoadablePlugin, report: LoadReport) -> bool:
    registrar = _Registrar(plugin_id)
    try:
        plugin.setup(registrar)
    except Exception as exc:
        report.fail(plugin_id, "failed", f"setup failed: {exc!r}")
        return False
    report.loaded[plugin_id] = Loaded(
        plugin,
        registrar.actions,
        registrar.jobs,
        frozenset(registrar.events),
        registrar.every,
        registrar.timeouts,
        frozenset(registrar.late),
        frozenset(registrar.subscriptions),
    )
    report.statuses[plugin_id] = LOADED
    return True


async def load(
    candidates: Sequence[Candidate],
    *,
    disabled: Collection[str],
    migrate_with: Migrator,
    lock: PluginLocks | None = None,
) -> LoadReport:
    report = LoadReport()
    plugins = _unique(candidates, report)
    for plugin_id in [p for p in plugins if p in disabled]:
        report.fail(plugin_id, "blocked")
        del plugins[plugin_id]
    await _lock(plugins, lock, report)
    unlock = await _migrate(plugins, migrate_with, report)
    unlock += [p for p, plugin in list(plugins.items()) if not _setup(p, plugin, report)]
    if lock is not None:
        for plugin_id in unlock:
            await lock.release_plugin(plugin_id)
    for problem in event_problems(report.loaded):
        log.warning("plugin event subscription: %s", problem)
    return report


def event_problems(loaded: Mapping[str, Loaded]) -> list[str]:
    """What is off between the loaded plugins' subscriptions and the events they declare: a subscription to an
    event no loaded plugin declares, which does nothing, and a field the subscriber's model needs that the
    emitter's payload doesn't have, which would fail every delivery."""
    declared = {
        f"{p}.{event.name}": event.model
        for p, found in loaded.items()
        for event in found.plugin.manifest.emits
    }
    problems: list[str] = []
    for plugin_id, found in loaded.items():
        for name, model in found.plugin.manifest.subscribes.items():
            emitter = declared.get(name)
            if emitter is None:
                problems.append(f"{plugin_id} subscribes to {name}, which no loaded plugin declares")
                continue
            missing = [
                field_name
                for field_name, info in model.model_fields.items()
                if info.is_required() and field_name not in emitter.model_fields
            ]
            if missing:
                problems.append(
                    f"{plugin_id} needs {', '.join(missing)} in {name}, which its payload doesn't have"
                )
    return problems
