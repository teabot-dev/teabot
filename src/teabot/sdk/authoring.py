"""Writing a plugin (D4, D45): a `Plugin`, and functions decorated as its actions and jobs.

```python
plugin = Plugin("giveaways", config=Cfg)
type Ctx = Context[Cfg]

@plugin.action(risk=WRITE, needs="manage_messages")
async def start(ctx: Ctx, channel: TextChannel, prize: str) -> Started:
    \"\"\"Start a giveaway.

    Args:
        channel: Where to announce it.
        prize: What the winner gets.
    \"\"\"
```

Each decorated function becomes an `ActionSpec` or a job handler when the plugin is set up, so the core keeps
one model of an action whichever way a plugin was written.
"""

import importlib.metadata
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Concatenate, Generic, Literal, Protocol, cast, get_args, overload

import discord
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python
from typing_extensions import TypeVar

from teabot.sdk import _clock as clock
from teabot.sdk._signature import (
    Inputs,
    Output,
    SignatureError,
    read_inputs,
    read_output,
    resolve,
    unalias,
    where,
)
from teabot.sdk._views import guild_view
from teabot.sdk.actions import (
    ACTION_TIMEOUT,
    MENU_NAME_MAX,
    SYSTEM,
    ActionContext,
    ActionError,
    ActionSpec,
    Actor,
    Choice,
    Menu,
    Risk,
    Surface,
)
from teabot.sdk.components import BoundAction
from teabot.sdk.db import Bound, Db
from teabot.sdk.events import FULL_NAME, Event, Received, declare
from teabot.sdk.guild import (
    Attachment,
    Guild,
    Member,
    Reaction,
    ReceivedMessage,
    RoleChange,
    User,
    VoiceChange,
    VoiceChannel,
)
from teabot.sdk.http import Http
from teabot.sdk.inputs import MODAL_MAX, asked
from teabot.sdk.labels import DEFAULT_FRESH, MAX_FRESH, WRITES, Labels
from teabot.sdk.members import READS, Members, NotDeclared
from teabot.sdk.permissions import Permission
from teabot.sdk.plans import Diff
from teabot.sdk.plugin import EmptyConfig, EventName, JobContext, JobHandler, Manifest, Registrar
from teabot.sdk.settings import InstanceSettings
from teabot.sdk.store import Store
from teabot.sdk.voice import Voice

READ = Risk.READ
WRITE = Risk.WRITE
DESTRUCTIVE = Risk.DESTRUCTIVE


Cfg = TypeVar("Cfg", bound=BaseModel, default=EmptyConfig)


class Context[C: BaseModel](Protocol):
    """What an action or a job receives, for one server (D2)."""

    @property
    def guild(self) -> Guild:
        """The server the call is about."""
        ...

    @property
    def actor(self) -> Actor:
        """Who made the call, and through which front door."""
        ...

    @property
    def config(self) -> C:
        """This server's config for the plugin (D10)."""
        ...

    @property
    def db(self) -> Db:
        """The plugin's own tables (D9). Every query filters by `guild_id`."""
        ...

    @property
    def now(self) -> datetime:
        """The current time, in UTC."""
        ...

    @property
    def diff(self) -> Diff:
        """In a destructive action's apply, the diff of the plan the member confirmed, whose entries' keys say what
        to change; empty elsewhere."""
        ...

    @property
    def filled(self) -> Mapping[str, Any]:
        """In an `@action.autocomplete` function, the other options the member has filled in so far, by input name,
        as Discord sent them; empty elsewhere. A suggester for a poll's option can read the poll chosen here."""
        ...

    @property
    def voice(self) -> Voice:
        """The server's voice connection, which the plugin joins, plays through and leaves (D54)."""
        ...

    @property
    def members(self) -> Members:
        """The server's member directory (D55), for a plugin that declares `Plugin(..., reads=["members"])`."""
        ...

    @property
    def labels(self) -> Labels:
        """The plugin's member labels in this server, for a plugin that declares `Plugin(..., writes=["labels"])`."""
        ...

    @property
    def audit_id(self) -> int | None:
        """In a destructive action's apply, its row in the audit log, which work it hands off to a job keeps as the
        job's grant; None elsewhere."""
        ...

    async def authorized(self, audit_id: int, /) -> Actor:
        """In a job, whether the work a member confirmed may still go on: the member, the door and the token the
        grant `audit_id` names, checked as a call is. Raises `NotAuthorized` with the reason when it may not."""
        ...

    async def record_outcome(self, audit_id: int, text: str, /, *, failed: bool = False) -> None:
        """In a job, close the handed-off apply `audit_id` with its outcome, once, in the audit log, which posts it
        to the server's log channel (D24)."""
        ...

    async def log(self, audit_id: int, text: str, /, *, failed: bool = False) -> None:
        """In a job, add a line under the handed-off apply `audit_id` to the audit log, posted to the server's log
        channel, as often as needed, for work that goes on under one grant, such as a rule that runs each day.
        `record_outcome` closes the apply; this does not."""
        ...

    async def report(self, progress: Mapping[str, Any], /) -> None:
        """Record how far a long change has got, which `/admin job_status` shows."""
        ...

    @property
    def http(self) -> Http:
        """The shared HTTP client, for the hosts the plugin declares in `Plugin(..., hosts=[...])` (D12)."""
        ...

    def store[M: BaseModel](self, model: type[M], *, kind: str | None = None) -> Store[M]:
        """This server's records of `model`, by key, with no SQL (D10). Not inside a transaction block."""
        ...


type Suggester[C: BaseModel, T: (str, int)] = Callable[[Context[C], str], Awaitable[list[Choice[T]]]]
"""What `@action.autocomplete(...)` decorates: it takes the context and what the member has typed."""

# How often a recurring job may run at most.
MIN_EVERY = timedelta(minutes=1)


@dataclass
class _Context:
    guild: Guild
    actor: Actor
    config: Any
    open_db: Callable[[], Db]
    reporter: Callable[[Mapping[str, Any]], Awaitable[None]]
    open_voice: Callable[[], Voice]
    diff: Diff = field(default_factory=Diff)
    now: datetime = field(default_factory=clock.now)
    plugin_id: str = ""
    hosts: frozenset[str] = frozenset()
    reads: frozenset[str] = frozenset()
    open_members: Callable[[], Members] | None = None
    audit_id: int | None = None
    grants: Any = None
    writes: frozenset[str] = frozenset()
    open_labels: Callable[[], Labels] | None = None
    filled: Mapping[str, Any] = field(default_factory=dict[str, Any])

    @property
    def db(self) -> Db:
        return self.open_db()

    async def authorized(self, audit_id: int, /) -> Actor:
        if self.grants is None:
            raise RuntimeError("grants are checked in jobs")
        actor: Actor = await self.grants.authorized(audit_id)
        return actor

    async def record_outcome(self, audit_id: int, text: str, /, *, failed: bool = False) -> None:
        if self.grants is None:
            raise RuntimeError("outcomes are recorded in jobs")
        await self.grants.record_outcome(audit_id, text, failed=failed)

    async def log(self, audit_id: int, text: str, /, *, failed: bool = False) -> None:
        if self.grants is None:
            raise RuntimeError("grant lines are logged in jobs")
        await self.grants.log(audit_id, text, failed=failed)

    @property
    def members(self) -> Members:
        if "members" not in self.reads:
            raise NotDeclared(
                f'{self.plugin_id} reads the member directory: declare Plugin(..., reads=["members"])'
            )
        if self.open_members is None:
            raise RuntimeError("this context has no member directory")
        return self.open_members()

    @property
    def labels(self) -> Labels:
        if "labels" not in self.writes:
            raise NotDeclared(
                f'{self.plugin_id} writes member labels: declare Plugin(..., writes=["labels"])'
            )
        if self.open_labels is None:
            raise RuntimeError("this context has no member labels")
        return self.open_labels()

    @property
    def http(self) -> Http:
        return Http(self.plugin_id, self.hosts)

    @property
    def voice(self) -> Voice:
        return self.open_voice()

    def store[M: BaseModel](self, model: type[M], *, kind: str | None = None) -> Store[M]:
        return Store(self.db, self.guild.id, model, kind or model.__name__)

    async def report(self, progress: Mapping[str, Any], /) -> None:
        await self.reporter(progress)


def _permissions(needs: Permission | tuple[Permission, ...]) -> discord.Permissions:
    names = (needs,) if isinstance(needs, str) else needs
    return discord.Permissions(**dict.fromkeys(names, True))


@dataclass
class Action[C: BaseModel, **P, R]:
    """A decorated action, which `@plugin.action` returns. `.plan` attaches a destructive action's plan.

    - `func` is the decorated function, and `name` its full name, such as `giveaways.start`.
    - `risk`, `needs` and `reply` are as the decorator was given them.
    - `inputs` and `output` are read from the function's signature and docstring.
    - `planner` is the plan `.plan` attached, if any.
    - `surfaces` are the front doors it is offered through.
    - `suggesters` are the functions `.autocomplete` attached, by the option each suggests values for.
    - `hosts` are the hosts its plugin declared, which its `ctx.http` reaches, `reads` what else it reads, such as
      `members`, which opens `ctx.members`, and `writes` what it writes, such as `labels`, which opens `ctx.labels`.
    - `menu` is its right-click command, if `menu=` made one.
    - `timeout` is how long a call may take before it is stopped.
    """

    func: Callable[Concatenate[Context[C], P], Awaitable[R]]
    name: str
    risk: Risk
    needs: discord.Permissions
    reply: Literal["private", "public"]
    inputs: Inputs
    output: Output
    planner: Callable[..., Awaitable[Diff]] | None = None
    surfaces: Surface = Surface.ALL
    suggesters: dict[str, Callable[..., Awaitable[list[Choice[Any]]]]] = field(
        default_factory=dict[str, Callable[..., Awaitable[list[Choice[Any]]]]]
    )
    hosts: frozenset[str] = frozenset()
    menu: Menu | None = None
    timeout: timedelta = ACTION_TIMEOUT
    reads: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()

    def autocomplete[T: (str, int)](
        self, option: str
    ) -> Callable[
        [Callable[[Context[C], str], Awaitable[list[Choice[T]]]]],
        Callable[[Context[C], str], Awaitable[list[Choice[T]]]],
    ]:
        """Suggest values for one of the action's options as the member types, such as running giveaways for
        `giveaway`. The function receives what has been typed so far, and returns up to 25 `Choice`s; the options
        filled in before it are `ctx.filled`. It runs as the member, without the audit log, and must answer within
        Discord's three seconds."""
        if option not in self.inputs.model.model_fields:
            raise SignatureError(f"{self.name} has no option {option} to suggest values for")

        def attach(
            func: Callable[[Context[C], str], Awaitable[list[Choice[T]]]],
        ) -> Callable[[Context[C], str], Awaitable[list[Choice[T]]]]:
            self.suggesters[option] = func
            return func

        return attach

    def plan(
        self, func: Callable[Concatenate[Context[C], P], Awaitable[Diff]]
    ) -> Callable[..., Awaitable[Diff]]:
        """Attach the plan of a destructive action: the change it would make, computed without making it (D21).
        It takes the same inputs as the action."""
        self.planner = func
        return func

    def spec(self) -> ActionSpec[Any, Any]:
        """The action as the core's model of one."""
        planner = self.planner
        return ActionSpec(
            self.name,
            self.inputs.summary,
            self.inputs.model,
            self.output.model,
            self.risk,
            self._run,
            default_permissions=self.needs,
            plan=self._plan if planner is not None else None,
            render=self.output.render,
            reply=self.reply,
            surfaces=self._surfaces(),
            suggest={option: self._suggester(option) for option in self.suggesters},
            menu=self.menu,
            timeout=self.timeout,
        )

    def _surfaces(self) -> Surface:
        """A destructive action's Confirm button is a press, so a command with a plan is pressable too."""
        if self.risk is DESTRUCTIVE and Surface.SLASH in self.surfaces:
            return self.surfaces | Surface.COMPONENT
        return self.surfaces

    def _suggester(self, option: str) -> Callable[[ActionContext, str], Awaitable[list[Choice[Any]]]]:
        func = self.suggesters[option]

        async def suggest(ctx: ActionContext, typed: str) -> list[Choice[Any]]:
            return list(await func(_context_of(ctx, self.name, self.hosts, self.reads, self.writes), typed))[
                :25
            ]

        return suggest

    def bind(self, *args: P.args, **kwargs: P.kwargs) -> BoundAction:
        """The action with its arguments fixed, for a `Button`. A `Role`, `TextChannel` or `Member` argument is kept
        as its ID, and found again when the button is pressed."""
        if args:
            raise TypeError(f"{self.name}.bind takes its arguments by name")
        payload = self.inputs.model.model_validate(self._raw(kwargs)).model_dump(
            mode="json", exclude_defaults=True
        )
        return BoundAction(self.name, payload)

    def asking(self, **kwargs: Any) -> BoundAction:
        """The action with some arguments fixed, for a `Button` that asks for the rest: pressing it opens a form for
        the required text inputs left out, up to five, and runs the action with what the member types. Every other
        required input must be given, as for `bind`."""
        raw = self._raw(kwargs)
        left_out = [f.name for f in asked(self.inputs.model, raw)]
        if not left_out:
            raise ValueError(f"{self.name}.asking leaves no text input out; use bind")
        if len(left_out) > MODAL_MAX:
            raise ValueError(
                f"{self.name}.asking leaves out {len(left_out)} text inputs; a form asks for at most {MODAL_MAX}"
            )
        try:
            self.inputs.model.model_validate(raw)
        except ValidationError as exc:
            wrong = [e for e in exc.errors() if not (e["type"] == "missing" and e["loc"][0] in left_out)]
            if wrong:
                raise ValueError(f"{self.name}.asking: {wrong[0]['loc'][0]}: {wrong[0]['msg']}") from exc
        return BoundAction(self.name, to_jsonable_python(raw))

    def _raw(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        refs = {r.name for r in self.inputs.resolved}
        return {k: _ids(v) if k in refs else v for k, v in kwargs.items()}

    async def _run(self, ctx: ActionContext, data: BaseModel) -> BaseModel:
        context = _context_of(ctx, self.name, self.hosts, self.reads, self.writes)
        arguments = await _arguments(self.inputs, context, data)
        func: Callable[..., Awaitable[Any]] = self.func
        return self.output.wrap(await func(**arguments))

    async def _plan(self, ctx: ActionContext, data: BaseModel) -> Diff:
        assert self.planner is not None
        return await self.planner(
            **await _arguments(
                self.inputs, _context_of(ctx, self.name, self.hosts, self.reads, self.writes), data
            )
        )


def _ids(value: Any) -> Any:
    """A Discord object as its ID, and a list of them as a list of IDs, as a slash command would send them."""
    if isinstance(value, list | tuple):
        return [str(getattr(item, "id", item)) for item in cast(list[Any], value)]
    return str(getattr(value, "id", value)) if value is not None else None


def _reason(action: str, actor: Actor) -> str:
    """What Discord's audit log shows for a change a plugin makes without its own reason."""
    who = "teabot" if actor.user_id == SYSTEM else f"member {actor.user_id}"
    return f"via teabot: {action}, for {who}"


def _menu_of(func: Callable[..., Any], name: str, menu: str, inputs: Inputs) -> Menu:
    """The right-click command `menu=` asks for, from the action's one input."""
    if not 1 <= len(menu) <= MENU_NAME_MAX:
        raise SignatureError(f"{where(func)}: {name}'s menu name is 1 to {MENU_NAME_MAX} characters")
    fields = list(inputs.model.model_fields)
    resolved = {r.name: r.kind for r in inputs.resolved if not r.many}
    if len(fields) != 1:
        raise SignatureError(
            f"{where(func)}: {name} has a menu, so it takes one input, the member or the message"
        )
    (only,) = fields
    if resolved.get(only) is Member:
        return Menu(menu, "user", only)
    if unalias(inputs.model.model_fields[only].annotation) is ReceivedMessage:
        return Menu(menu, "message", only)
    raise SignatureError(f"{where(func)}: {name} has a menu, so its input is a Member or a ReceivedMessage")


def _members_of(ctx: Any) -> Callable[[], Members] | None:
    """The core's `ctx.members` for a context, when it has one."""
    opener = getattr(ctx, "open_members", None)
    return cast(Callable[[], Members], opener) if callable(opener) else None


def _labels_of(ctx: Any) -> Callable[[], Labels] | None:
    """The core's `ctx.labels` for a context, when it has one."""
    opener = getattr(ctx, "open_labels", None)
    return cast(Callable[[], Labels], opener) if callable(opener) else None


def _context_of(
    ctx: ActionContext,
    action: str,
    hosts: frozenset[str],
    reads: frozenset[str] = frozenset(),
    writes: frozenset[str] = frozenset(),
) -> _Context:
    diff = getattr(ctx, "diff", None) or Diff()
    guild = guild_view(ctx.guild, _reason(action, ctx.actor), action)
    return _Context(
        guild,
        ctx.actor,
        ctx.config,
        lambda: ctx.db,
        ctx.report,
        lambda: ctx.voice,
        diff,
        plugin_id=action.split(".", maxsplit=1)[0],
        hosts=hosts,
        reads=reads,
        open_members=_members_of(ctx),
        audit_id=getattr(ctx, "audit_id", None),
        writes=writes,
        open_labels=_labels_of(ctx),
        filled=getattr(ctx, "filled", None) or {},
    )


async def _arguments(inputs: Inputs, context: _Context, data: BaseModel) -> dict[str, Any]:
    arguments = await resolve(context.guild, inputs, data)
    for name in inputs.injected:
        arguments[name] = context
    return arguments


@dataclass
class Job[C: BaseModel, **P]:
    """A decorated job, which `@plugin.job` returns. Its parameters, after the context, are its payload.

    - `func` is the decorated function, and `name` the job's name within the plugin.
    - `inputs` is the payload's model, read from the function's signature.
    - `every` is how often it runs on its own, or None for a job that runs when scheduled.
    - `plugin_id` is its plugin's id, `hosts` the hosts its plugin declared, which its `ctx.http` reaches, `reads`
      what else it reads, such as `members`, and `writes` what it writes, such as `labels`.
    - `timeout` is how long a run may take before it is stopped, or None for teabot's fifteen minutes.
    """

    func: Callable[Concatenate[Context[C], P], Awaitable[None]]
    name: str
    inputs: Inputs
    every: timedelta | None = None
    plugin_id: str = ""
    hosts: frozenset[str] = frozenset()
    timeout: timedelta | None = None
    reads: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()

    def bind(self, *args: P.args, **kwargs: P.kwargs) -> Bound:
        """The job with its arguments fixed, for `tx.schedule` and `tx.unschedule`. Its key is the job's name and
        arguments, so scheduling the same bound job again moves the pending one."""
        if args:
            raise TypeError(f"{self.name}.bind takes its arguments by name")
        payload = self.inputs.model.model_validate(kwargs).model_dump(mode="json")
        return Bound(self.name, payload, f"{self.name}:{json.dumps(payload, sort_keys=True)}")

    async def _handle(self, job: JobContext) -> None:
        guild = job.client.get_guild(job.guild_id) if job.guild_id is not None else None
        if guild is None:
            raise ActionError("The bot is no longer in this job's server.")
        actor = Actor(job.started_by or SYSTEM, "job")
        context = _Context(
            guild_view(guild, _reason(self.name, actor), self.name),
            actor,
            job.config,
            lambda: job.db,
            job.report,
            lambda: job.voice,
            plugin_id=self.plugin_id,
            hosts=self.hosts,
            reads=self.reads,
            open_members=_members_of(job),
            grants=getattr(job, "grants", None),
            writes=self.writes,
            open_labels=_labels_of(job),
        )
        data = self.inputs.model.model_validate(dict(job.payload))
        func: Callable[..., Awaitable[None]] = self.func
        await func(**await _arguments(self.inputs, context, data))


class Plugin(Generic[Cfg]):
    """A plugin: its id, its per-server config, and the actions and jobs decorated with it.

    The version comes from the plugin's package, and migrations from a `migrations/` directory beside it.
    `description`, a sentence on what it does, shows in the instance's plugin browser.
    """

    # Python 3.12 has no syntax for a type parameter's default, so this uses Generic and typing_extensions.
    def __init__(
        self,
        id: str,
        *,
        config: type[Cfg] = EmptyConfig,
        migrations: Path | None = None,
        hosts: Iterable[str] = (),
        description: str = "",
        reads: Iterable[str] = (),
        writes: Iterable[str] = (),
        labels_fresh_for: timedelta = DEFAULT_FRESH,
        emits: Iterable[Event] = (),
    ) -> None:
        self.id = id
        self.description = description
        self.hosts = frozenset(hosts)
        self.reads = frozenset(reads)
        if unknown := self.reads - READS:
            raise SignatureError(f"{id}: Plugin(reads=...) takes {sorted(READS)}, not {sorted(unknown)}")
        self.writes = frozenset(writes)
        if unknown := self.writes - WRITES:
            raise SignatureError(f"{id}: Plugin(writes=...) takes {sorted(WRITES)}, not {sorted(unknown)}")
        if not timedelta(0) < labels_fresh_for <= MAX_FRESH:
            raise SignatureError(
                f"{id}: Plugin(labels_fresh_for=...) is more than nothing and at most a week"
            )
        self.labels_fresh_for = labels_fresh_for
        self.emits = tuple(emits)
        declare(id, list(self.emits))
        # The plugin events it subscribes to, each with its own copy of the payload model.
        self.subscriptions: dict[str, tuple[type[BaseModel], Callable[..., Awaitable[None]]]] = {}
        self.config = config
        self._migrations = migrations
        self._module: str | None = None
        self._version: str | None = None
        self.home = _caller_module()
        self.actions: dict[str, Action[Cfg, ..., Any]] = {}
        self.jobs: dict[str, Job[Cfg, ...]] = {}
        self.listeners: dict[EventName, Callable[..., Awaitable[None]]] = {}
        # The events whose handler also hears changes the member directory found late (D55).
        self.late: set[EventName] = set()
        self.settings: list[InstanceSettings[Any]] = []

    def action[**P, R](
        self,
        *,
        risk: Risk = WRITE,
        needs: Permission | tuple[Permission, ...] = (),
        name: str | None = None,
        reply: Literal["private", "public"] = "private",
        surfaces: Surface = Surface.ALL,
        menu: str | None = None,
        timeout: timedelta = ACTION_TIMEOUT,
    ) -> Callable[[Callable[Concatenate[Context[Cfg], P], Awaitable[R]]], Action[Cfg, P, R]]:
        """Make a function an action: a slash command, an MCP tool, a service route, and what a button or select
        menu runs, at once (D1).

        Its name is the function's, without a trailing underscore, unless `name` says otherwise. Its docstring's
        first line is the summary, and its `Args:` section describes each input. `surfaces` narrows where it is
        offered: `Surface.COMPONENT` for an action only its buttons and menus reach.

        `menu="Give a pet"` also makes it a right-click command, named so in Discord's Apps menu, on a member when
        its one input is a `Member`, or on a message when it is a `ReceivedMessage`. An action on a message is not
        a slash command, since no slash command can give one.

        `timeout` is how long a call may take before teabot stops it and tells the member, a minute unless given.
        """

        def register(func: Callable[Concatenate[Context[Cfg], P], Awaitable[R]]) -> Action[Cfg, P, R]:
            action_name = f"{self.id}.{name or func.__name__.removesuffix('_')}"
            self._check_context(func, action_name)
            inputs = read_inputs(func, action_name, injected={Context: "context"})
            found_menu = _menu_of(func, action_name, menu, inputs) if menu is not None else None
            offered = surfaces
            if found_menu is not None:
                offered = surfaces | Surface.MENU
                if found_menu.kind == "message":
                    offered &= ~Surface.SLASH
            built = Action(
                func,
                action_name,
                risk,
                _permissions(needs),
                reply,
                inputs,
                read_output(func, action_name),
                surfaces=offered,
                hosts=self.hosts,
                menu=found_menu,
                timeout=timeout,
                reads=self.reads,
                writes=self.writes,
            )
            self.actions[action_name] = built
            self._module = self._module or func.__module__
            return built

        return register

    @overload
    def job[**P](self, func: Callable[Concatenate[Context[Cfg], P], Awaitable[None]], /) -> Job[Cfg, P]: ...
    @overload
    def job(
        self,
        func: None = None,
        /,
        *,
        name: str | None = None,
        every: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> "NamedJob[Cfg]": ...
    def job(
        self,
        func: Any = None,
        /,
        *,
        name: str | None = None,
        every: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> Any:
        """Make a function one of the plugin's jobs: `@plugin.job`, or `@plugin.job(name="...")` to keep a name
        that jobs already queued were stored under. Its parameters, after the context, are its payload.

        `@plugin.job(every=timedelta(days=1))` runs it on its own, every day, in each server where the plugin
        is enabled. A recurring job takes no parameters after the context.

        `timeout` is how long a run may take before teabot stops it and retries it later, fifteen minutes unless
        given."""
        if func is None:
            return NamedJob(self, name, every, timeout)
        return self.register_job(func, name)

    def register_job[**P](
        self,
        func: Callable[Concatenate[Context[Cfg], P], Awaitable[None]],
        name: str | None = None,
        every: timedelta | None = None,
        timeout: timedelta | None = None,
    ) -> Job[Cfg, P]:
        """Register a job; `@plugin.job` is the usual way to call this."""
        job_name = name or func.__name__
        self._check_context(func, f"{self.id}.{job_name}")
        inputs = read_inputs(func, f"{self.id}.{job_name}", injected={Context: "context"}, needs_docs=False)
        if every is not None:
            if every < MIN_EVERY:
                raise SignatureError(
                    f"{where(func)}: {self.id}.{job_name} runs every {every}; the least is a minute"
                )
            if inputs.model.model_fields:
                raise SignatureError(
                    f"{where(func)}: {self.id}.{job_name} runs on its own, so it takes no parameters after ctx"
                )
        built = Job(func, job_name, inputs, every, self.id, self.hosts, timeout, self.reads, self.writes)
        self.jobs[job_name] = built
        self._module = self._module or func.__module__
        return built

    def _check_context(self, func: Callable[..., Any], name: str) -> None:
        params = list(inspect.signature(func).parameters.values())
        hint = unalias(params[0].annotation) if params else None
        configured = get_args(hint)
        wanted = configured[0] if configured else Any
        if wanted is not Any and isinstance(wanted, type) and wanted is not self.config:
            raise SignatureError(
                f"{where(func)}: {name} takes Context[{wanted.__name__}], but the plugin's config is "
                f"{self.config.__name__}. Use the plugin's Ctx alias."
            )

    def locate(self, module: str, version: str | None) -> None:
        """Called by the loader with the entry point's module and its distribution's version."""
        self._module = module
        self._version = version

    @property
    def manifest(self) -> Manifest:
        """What the core needs before loading the plugin, worked out from its package."""
        return Manifest(
            self.id,
            self._version or _version_of(self._module),
            self.config,
            self._migrations_dir(),
            self.description,
            self.reads,
            self.writes,
            self.labels_fresh_for,
            self.emits,
            {name: model for name, (model, _) in self.subscriptions.items()},
        )

    def _migrations_dir(self) -> Path | None:
        if self._migrations is not None:
            return self._migrations
        module = sys.modules.get(self._module or "")
        file = getattr(module, "__file__", None)
        if file is None:
            return None
        found = Path(file).parent / "migrations"
        return found if found.is_dir() else None

    def instance_settings[M: BaseModel](self, model: type[M]) -> InstanceSettings[M]:
        """Settings for the whole instance, read from `TEABOT_PLUGIN_<ID>_<FIELD>` when the plugin loads. Keep
        what returns at module level, and call its `get()` where the settings are needed."""
        found = InstanceSettings(self.id, model)
        self.settings.append(found)
        return found

    def setup(self, registrar: Registrar) -> None:
        """Register every decorated action and job with the core, once the instance settings are read."""
        for settings in self.settings:
            settings.load()
        for action in self.actions.values():
            if action.risk is DESTRUCTIVE and action.planner is None:
                raise SignatureError(
                    f"{where(action.func)}: {action.name} is destructive and needs a plan. "
                    f"Decorate a function with @{action.func.__name__}.plan (D21)."
                )
            registrar.action(action.spec())
        for job in self.jobs.values():
            registrar.job(job.name, job._handle, job.timeout)  # pyright: ignore[reportPrivateUsage]
            if job.every is not None:
                registrar.recurring(job.name, job.every)
        for event, func in self.listeners.items():
            registrar.event(event, self._listener(event, func), late=event in self.late)
        for name, (model, func) in self.subscriptions.items():
            registrar.subscribe(name, self._subscriber(name, model, func))

    def on_event[M: BaseModel](
        self, name: str, model: type[M]
    ) -> Callable[
        [Callable[[Context[Cfg], Received[M]], Awaitable[None]]],
        Callable[[Context[Cfg], Received[M]], Awaitable[None]],
    ]:
        """Run the function when a plugin emits the event `name`, `<plugin>.<event>`, in a server where this plugin
        is enabled, with a `Received[model]`. `model` is this plugin's own copy of the payload model, since plugins
        never import each other. It runs as a job, retried over about two hours.

        Delivery is at least once and in no order across events: check `event.id` before changing anything, and
        keep the last `id` applied for each thing. A handler hears what already happened and can't undo it. It
        runs as the system, so it changes what the plugin may change on its own: its tables, labels and posts.
        A member ID in the payload must be a `MemberRef`, or deleting that member's data misses it."""
        if not FULL_NAME.fullmatch(name):
            raise SignatureError(
                f"{self.id}: subscribe to an event by its full name, such as tickets.accepted"
            )

        def register(
            func: Callable[[Context[Cfg], Received[M]], Awaitable[None]],
        ) -> Callable[[Context[Cfg], Received[M]], Awaitable[None]]:
            if name in self.subscriptions:
                raise SignatureError(f"{where(func)}: {self.id} already subscribes to {name}")
            self._check_context(func, f"{self.id}.on_event:{name}")
            self.subscriptions[name] = (model, func)
            self._module = self._module or func.__module__
            return func

        return register

    @overload
    def on_member_join(
        self, func: Callable[[Context[Cfg], Member], Awaitable[None]], /
    ) -> Callable[[Context[Cfg], Member], Awaitable[None]]: ...
    @overload
    def on_member_join(
        self, func: None = None, /, *, late: Literal[True]
    ) -> "LateDecorator[Context[Cfg], Member]": ...
    def on_member_join(self, func: Any = None, /, *, late: bool = False) -> Any:
        """Run `func` when a member joins a server where the plugin is enabled, with the member. It runs as a job,
        so a failure is retried, and a member who has left again by then is skipped.

        With `late=True`, `func` also hears joins teabot found later, such as while it restarted, and takes a third
        argument: when the gap began, or None for a join heard as it happened (D55)."""
        return self._listen_maybe_late("member_join", func, late)

    @overload
    def on_member_leave(
        self, func: Callable[[Context[Cfg], User], Awaitable[None]], /
    ) -> Callable[[Context[Cfg], User], Awaitable[None]]: ...
    @overload
    def on_member_leave(
        self, func: None = None, /, *, late: Literal[True]
    ) -> "LateDecorator[Context[Cfg], User]": ...
    def on_member_leave(self, func: Any = None, /, *, late: bool = False) -> Any:
        """Run `func` when a member leaves, or is kicked or banned from, a server where the plugin is enabled, with
        the `User` they were: their ID and username, since they are no longer a member. It runs as a job.

        With `late=True`, `func` also hears leaves teabot found later, and takes when the gap began, or None."""
        return self._listen_maybe_late("member_leave", func, late)

    def on_message(
        self, func: Callable[[Context[Cfg], ReceivedMessage], Awaitable[None]]
    ) -> Callable[[Context[Cfg], ReceivedMessage], Awaitable[None]]:
        """Run `func` for each message posted in a server where the plugin is enabled. Messages from bots,
        teabot's own among them, are not heard. Its text arrives only with the Message Content intent on. It
        runs as a job, so keep it quick: every message in a busy server queues one."""
        return self._listen("message", func)

    def on_reaction_add(
        self, func: Callable[[Context[Cfg], Reaction], Awaitable[None]]
    ) -> Callable[[Context[Cfg], Reaction], Awaitable[None]]:
        """Run `func` when someone other than a bot adds a reaction to any message, in a server where the
        plugin is enabled. It runs as a job."""
        return self._listen("reaction_add", func)

    def on_reaction_remove(
        self, func: Callable[[Context[Cfg], Reaction], Awaitable[None]]
    ) -> Callable[[Context[Cfg], Reaction], Awaitable[None]]:
        """Run `func` when someone takes a reaction off a message. It runs as a job."""
        return self._listen("reaction_remove", func)

    @overload
    def on_member_roles(
        self, func: Callable[[Context[Cfg], RoleChange], Awaitable[None]], /
    ) -> Callable[[Context[Cfg], RoleChange], Awaitable[None]]: ...
    @overload
    def on_member_roles(
        self, func: None = None, /, *, late: Literal[True]
    ) -> "LateDecorator[Context[Cfg], RoleChange]": ...
    def on_member_roles(self, func: Any = None, /, *, late: bool = False) -> Any:
        """Run `func` when a member's roles change, with the roles added and removed. It runs as a job. Where the
        member directory runs, every member's changes are heard; while a server's directory is still filling, or
        is off, only those of members teabot has seen since it started (D55).

        With `late=True`, `func` also hears changes teabot found later, and takes when the gap began, or None."""
        return self._listen_maybe_late("member_roles", func, late)

    def on_voice(
        self, func: Callable[[Context[Cfg], VoiceChange], Awaitable[None]]
    ) -> Callable[[Context[Cfg], VoiceChange], Awaitable[None]]:
        """Run `func` when a member joins, leaves or moves between voice channels. Muting and deafening are not
        heard. It runs as a job."""
        return self._listen("voice", func)

    def on_enable(
        self, func: Callable[[Context[Cfg]], Awaitable[None]]
    ) -> Callable[[Context[Cfg]], Awaitable[None]]:
        """Run `func` when a server turns the plugin on, such as to create what it needs there. It runs again
        each time the plugin is turned back on, so check what is already there first. It runs as a job."""
        return self._listen("enable", func)

    def on_labels_changed(
        self, func: Callable[[Context[Cfg], str], Awaitable[None]]
    ) -> Callable[[Context[Cfg], str], Awaitable[None]]:
        """Run `func` when a plugin's member labels change in a server where this plugin is enabled, with that
        plugin's ID: when its `replace_all` changes anyone's, when its labels become fresh again, or when it is
        turned back on. It runs as a job, once for each change."""
        return self._listen("labels_changed", func)

    def on_track_end(
        self, func: Callable[[Context[Cfg], str, str | None], Awaitable[None]]
    ) -> Callable[[Context[Cfg], str, str | None], Awaitable[None]]:
        """Run `func` when audio the plugin played with `ctx.voice.play` runs out or fails, with the `track` it was
        given and the error, or None when it simply ended (D54). Audio the plugin stopped or replaced does not
        count. It runs as a job in the process holding the connection, so it can play the next track."""
        return self._listen("track_end", func)

    def on_voice_resume(
        self, func: Callable[[Context[Cfg], VoiceChannel | None], Awaitable[None]]
    ) -> Callable[[Context[Cfg], VoiceChannel | None], Awaitable[None]]:
        """Run `func` after a restart in each server whose voice channel the plugin held, with that channel, or None
        when it is gone (D54). The restart dropped the connection, so the plugin joins again to carry on, or
        leaves to give the connection up."""
        return self._listen("voice_resume", func)

    def on_voice_alone(
        self, func: Callable[[Context[Cfg], VoiceChannel | None], Awaitable[None]]
    ) -> Callable[[Context[Cfg], VoiceChannel | None], Awaitable[None]]:
        """Run `func` when the last member other than bots leaves the voice channel the plugin holds, with the
        channel (D54). It runs as a job; someone may have come back by then, so check `channel.listener_ids`
        before leaving, or schedule a job of the plugin's own to check after a grace period."""
        return self._listen("voice_alone", func)

    def _listen_maybe_late(self, event: EventName, func: Any, late: bool) -> Any:
        if func is not None:
            return self._listen(event, func)

        def register(handler: Any) -> Any:
            self._listen(event, handler)
            if late:
                self.late.add(event)
            return handler

        return register

    def _listen[F: Callable[..., Awaitable[None]]](self, event: EventName, func: F) -> F:
        if event in self.listeners:
            raise SignatureError(f"{where(func)}: {self.id} already handles {event}")
        self._check_context(func, f"{self.id}.on_{event}")
        self.listeners[event] = func
        self._module = self._module or func.__module__
        return func

    def _event_context(self, job: JobContext, name: str) -> "_Context | None":
        """The context a handler of an event runs with, as the system; None when the bot has left the server."""
        guild = job.client.get_guild(job.guild_id) if job.guild_id is not None else None
        if guild is None:
            return None
        actor = Actor(SYSTEM, "event")
        return _Context(
            guild_view(guild, _reason(name, actor), name),
            actor,
            job.config,
            lambda: job.db,
            job.report,
            lambda: job.voice,
            plugin_id=self.id,
            hosts=self.hosts,
            reads=self.reads,
            open_members=_members_of(job),
            grants=getattr(job, "grants", None),
            writes=self.writes,
            open_labels=_labels_of(job),
        )

    def _subscriber(
        self, event: str, model: type[BaseModel], func: Callable[..., Awaitable[None]]
    ) -> JobHandler:
        name = f"{self.id}.on_event:{event}"

        async def handle(job: JobContext) -> None:
            context = self._event_context(job, name)
            if context is None:
                return
            found = job.payload["event"]
            by = found.get("by")
            received = Received(
                int(found["id"]),
                str(found["source"]),
                str(found["name"]),
                datetime.fromisoformat(str(found["at"])),
                int(by) if by is not None else None,
                model.model_validate(found["data"]),
            )
            await func(context, received)

        return handle

    def _listener(self, event: EventName, func: Callable[..., Awaitable[None]]) -> JobHandler:
        name = f"{self.id}.on_{event}"

        async def handle(job: JobContext) -> None:
            guild = job.client.get_guild(job.guild_id) if job.guild_id is not None else None
            if guild is None:
                return
            actor = Actor(SYSTEM, "event")
            context = _Context(
                guild_view(guild, _reason(name, actor), name),
                actor,
                job.config,
                lambda: job.db,
                job.report,
                lambda: job.voice,
                plugin_id=self.id,
                hosts=self.hosts,
                reads=self.reads,
                open_members=_members_of(job),
                grants=getattr(job, "grants", None),
                writes=self.writes,
                open_labels=_labels_of(job),
            )
            if event == "enable":
                await func(context)
                return
            if event == "labels_changed":
                await func(context, str(job.payload["plugin"]))
                return
            if event == "track_end":
                error = job.payload.get("error")
                await func(context, str(job.payload["track"]), str(error) if error is not None else None)
                return
            if event in ("voice_resume", "voice_alone"):
                await func(context, context.guild.get_voice_channel(int(job.payload["channel_id"])))
                return
            received = await _received(event, context.guild, job.payload)
            if received is None:
                return
            if event in self.late:
                since = job.payload.get("since")
                await func(context, received, datetime.fromisoformat(since) if since else None)
            else:
                await func(context, received)

        return handle


def _role_ids(values: Any) -> tuple[int, ...]:
    return tuple(int(v) for v in values or ())


def _id(value: Any) -> int | None:
    return None if value is None else int(value)


async def _received(event: EventName, guild: Guild, payload: Mapping[str, Any]) -> Any:
    """What a handler for `event` receives, from the event's job payload, or None when there is nothing to hand
    it, such as a member who has left since."""
    if event == "message":
        return ReceivedMessage(
            int(payload["message_id"]),
            int(payload["channel_id"]),
            int(payload["author_id"]),
            str(payload["content"]),
            tuple(Attachment(**a) for a in payload.get("attachments", ())),
        )
    if event in ("reaction_add", "reaction_remove"):
        return Reaction(
            int(payload["message_id"]),
            int(payload["channel_id"]),
            int(payload["user_id"]),
            str(payload["emoji"]),
        )
    user_id = int(payload["user_id"])
    if event == "member_leave":
        return User(user_id, str(payload.get("name") or user_id))
    member = await guild.get_member(user_id)
    return None if member is None else _about_member(event, member, payload)


def _about_member(event: EventName, member: Member, payload: Mapping[str, Any]) -> Any:
    """What a handler for an event about a member who is still here receives."""
    if event == "member_roles":
        return RoleChange(member, _role_ids(payload.get("added")), _role_ids(payload.get("removed")))
    if event == "voice":
        return VoiceChange(member, _id(payload.get("left")), _id(payload.get("joined")))
    return member


type LateDecorator[C, T] = Callable[
    [Callable[[C, T, datetime | None], Awaitable[None]]], Callable[[C, T, datetime | None], Awaitable[None]]
]
"""`@plugin.on_member_join(late=True)` and its kin: a decorator for a handler that also takes when a gap began."""


@dataclass(frozen=True)
class NamedJob[C: BaseModel]:
    """`@plugin.job(name="...", every=...)`: a job decorator with a given name, or one that recurs."""

    plugin: Plugin[C]
    name: str | None
    every: timedelta | None = None
    timeout: timedelta | None = None

    def __call__[**P](self, func: Callable[Concatenate[Context[C], P], Awaitable[None]]) -> Job[C, P]:
        return self.plugin.register_job(func, self.name, self.every, self.timeout)


def _caller_module() -> str:
    """The module that made the plugin, past any `__init__` of a `Plugin` subclass."""
    frame = sys._getframe(2)  # pyright: ignore[reportPrivateUsage]
    while frame.f_back is not None and frame.f_code.co_name == "__init__":
        frame = frame.f_back
    return str(frame.f_globals.get("__name__", ""))


def _version_of(module: str | None) -> str:
    """The version of the installed distribution that holds `module`, or 0.0.0 for code that is not one."""
    top = (module or "").split(".", 1)[0]
    for dist in importlib.metadata.packages_distributions().get(top, []):
        return importlib.metadata.version(dist)
    return "0.0.0"
