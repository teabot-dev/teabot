"""The action registry and its one call path (D1, D2, D18, D19, D24).

Every front door calls `Registry.invoke`. In order: resolve the server, check the plugin is enabled there,
validate the input, check the member's live permissions, audit, run, and record the outcome. A refusal after
the action is known is audited once with the outcome `refused`. Token checks (D17, D20) and destructive plans
(D21) slot in here.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Literal, Protocol

import discord
from pydantic import BaseModel, ValidationError

from teabot.core.config import ConfigError, ConfigStore
from teabot.core.directory.labels import LabelService
from teabot.core.directory.plugin_api import GuildMembers
from teabot.core.loader import Loaded
from teabot.core.voice import VoiceService
from teabot.db.pool import GuildPluginDatabase, Pools
from teabot.sdk import ActionError, ActionSpec, Actor, Choice, Risk, Surface
from teabot.sdk.plans import Diff, HandedOff

log = logging.getLogger(__name__)
# Discord waits three seconds for suggestions, and the reply itself takes some of them.
SUGGEST_SECONDS = 2.5

type RefusalCode = Literal[
    "unknown_action",
    "not_in_guild",
    "wrong_surface",
    "above_token_risk",
    "not_allowed_by_token",
    "plugin_not_enabled",
    "config_invalid",
    "invalid_input",
    "forbidden",
    "confirm_unknown",
    "confirm_mismatch",
    "plan_expired",
    "plan_used",
]

# The front door a call came through, and the surface an action must offer for it (D1).
SURFACE_OF = {
    "slash": Surface.SLASH,
    "component": Surface.COMPONENT,
    "mcp": Surface.MCP,
    "service": Surface.SERVICE,
    "menu": Surface.MENU,
    "web": Surface.WEB,
}


@dataclass(frozen=True)
class TokenLimits:
    """What a token may reach beyond its creator's live permissions: a maximum risk (D17) and, when not
    empty, an allow-list of action names (D20)."""

    max_risk: Risk
    allowed: frozenset[str] = frozenset()


def door_refusal(spec: "ActionSpec[Any, Any]", via: str, limits: TokenLimits | None) -> "Refused | None":
    """Why this front door or token may not reach the action, before anything else is looked up."""
    if SURFACE_OF[via] not in spec.surfaces:
        return Refused("wrong_surface", f"{spec.name} is not offered through {via}")
    if limits is None:
        return None
    if spec.risk > limits.max_risk:
        return Refused("above_token_risk", f"this token may not use {spec.risk.name.lower()} actions")
    if limits.allowed and spec.name not in limits.allowed:
        return Refused("not_allowed_by_token", f"this token may not use {spec.name}")
    return None


ENABLED = "SELECT enabled FROM core.guild_plugins WHERE guild_id = $1 AND plugin_id = $2"


class Refused(Exception):
    """The call was not allowed to run. `code` says why; `message` is safe to show the caller."""

    def __init__(self, code: RefusalCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code: RefusalCode = code
        self.message = message


class ActionFailed(Exception):
    """The action ran and raised. The audit row holds the error. `public_message` is set when the action
    raised `ActionError`, whose message is meant for the member."""

    def __init__(self, error: str, public_message: str | None = None) -> None:
        super().__init__(error)
        self.public_message = public_message


@dataclass(frozen=True)
class Call:
    action: str
    guild_id: int
    actor: Actor
    raw_input: Mapping[str, Any]
    token: TokenLimits | None = None


@dataclass(frozen=True)
class CallRecord:
    """What the audit log records about one call (D24)."""

    action: str
    guild_id: int
    actor: Actor
    input: Mapping[str, Any]
    risk: Risk


class Audit(Protocol):
    async def started(self, call: CallRecord) -> int: ...

    async def finished(self, audit_id: int, outcome: str, error: str | None) -> None: ...

    async def single(self, call: CallRecord, outcome: str, error: str | None) -> None: ...


class PlanBook(Protocol):
    """Plan then apply (D21), in `teabot.core.plans`: a destructive call plans, or applies a confirmed plan."""

    async def handle(
        self,
        spec: "ActionSpec[Any, Any]",
        ctx: "PluginActionContext",
        data: BaseModel,
        record: CallRecord,
        confirm: str | None,
    ) -> BaseModel: ...


class Member(Protocol):
    @property
    def guild_permissions(self) -> discord.Permissions: ...


class Guild(Protocol):
    @property
    def id(self) -> int: ...

    def get_member(self, user_id: int, /) -> Member | None: ...

    async def fetch_member(self, user_id: int, /) -> Member: ...


class GuildSource(Protocol):
    def get_guild(self, guild_id: int, /) -> Guild | None: ...


@dataclass(frozen=True)
class PluginActionContext:
    """The `ActionContext` one call receives, for one server (D2). The plugin's database is opened on first
    use, so a built-in action, which works through core services, never needs a plugin schema."""

    guild: Any
    actor: Actor
    config: BaseModel
    open_db: Callable[[], GuildPluginDatabase] | None
    reporter: Callable[[Mapping[str, Any]], Awaitable[None]] | None = None
    diff: Diff | None = None
    open_voice: Callable[[], Any] | None = None
    # The server's member directory, as `ctx.members` (D55); the SDK checks the plugin declared it.
    open_members: Callable[[], Any] | None = None
    # The plugin's member labels, as `ctx.labels`; the SDK checks the plugin declared them.
    open_labels: Callable[[], Any] | None = None
    # In an apply, its audit row, which work it hands off keeps as its grant.
    audit_id: int | None = None
    # In a suggester, the other options the member has filled in, by input name.
    filled: Mapping[str, Any] = field(default_factory=dict[str, Any])

    async def report(self, progress: Mapping[str, Any], /) -> None:
        """A destructive action's progress, written to its apply job; elsewhere nothing (D21)."""
        if self.reporter is not None:
            await self.reporter(progress)

    @property
    def db(self) -> GuildPluginDatabase:
        if self.open_db is None:
            raise RuntimeError("built-in actions work through core services, not ctx.db")
        return self.open_db()

    @property
    def queue(self) -> GuildPluginDatabase:
        return self.db

    @property
    def voice(self) -> Any:
        if self.open_voice is None:
            raise RuntimeError("built-in actions have no voice connection")
        return self.open_voice()


@dataclass(frozen=True)
class ActionSource:
    """What the registry needs from one plugin: its config model, its actions, and how it is enabled.

    A built-in plugin is always enabled and has no schema; an installed one is enabled per server and acts
    as its own role in its own schema (D9).
    """

    config: type[BaseModel]
    actions: Mapping[str, ActionSpec[Any, Any]]
    builtin: bool = False


async def find_member(guild: Guild, user_id: int) -> Member:
    """A member, from the cache or over REST when not cached (D18, D25)."""
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            raise Refused("forbidden", "you are not a member of this server") from None
    return member


async def member_permissions(guild: Guild, user_id: int) -> discord.Permissions:
    """A member's current permissions (D18)."""
    return (await find_member(guild, user_id)).guild_permissions


def _handed_off(result: Any) -> bool:
    """Whether an apply handed its work off, returning `HandedOff`, whatever its output model wraps it in."""
    return isinstance(result, HandedOff) or isinstance(getattr(result, "value", None), HandedOff)


def split_confirm(spec: ActionSpec[Any, Any], call: Call) -> tuple[str | None, Call]:
    """A destructive call's `confirm` value, which the core adds to its input (D21), and the call without it."""
    if spec.risk is not Risk.DESTRUCTIVE or "confirm" not in call.raw_input:
        return None, call
    rest = {k: v for k, v in call.raw_input.items() if k != "confirm"}
    return str(call.raw_input["confirm"]), replace(call, raw_input=rest)


def sources_from(loaded: Mapping[str, Loaded]) -> dict[str, ActionSource]:
    return {plugin_id: ActionSource(p.plugin.manifest.config, p.actions) for plugin_id, p in loaded.items()}


class Registry:
    def __init__(
        self,
        sources: Mapping[str, ActionSource],
        *,
        pools: Pools,
        client: GuildSource,
        audit: Audit,
        plans: PlanBook | None = None,
    ) -> None:
        self.sources = sources
        # The voice service gives plugin calls `ctx.voice` (D54); `assemble` sets it.
        self.voice: VoiceService | None = None
        self.labels: LabelService | None = None
        self.pools = pools
        self.config = ConfigStore(pools.core_db())
        self.client = client
        self.audit = audit
        self.plans = plans
        self.actions: dict[str, ActionSpec[Any, Any]] = {
            name: spec for source in sources.values() for name, spec in source.actions.items()
        }

    async def invoke(self, call: Call) -> BaseModel:
        spec = self.actions.get(call.action)
        if spec is None:
            raise Refused("unknown_action", f"there is no action {call.action}")
        guild = self.client.get_guild(call.guild_id)
        if guild is None:
            raise Refused("not_in_guild", "the bot is not in that server")
        confirm, call = split_confirm(spec, call)
        record = CallRecord(call.action, call.guild_id, call.actor, call.raw_input, spec.risk)
        try:
            ctx, data = await self.prepare(spec, call, guild)
            if spec.risk is Risk.DESTRUCTIVE:
                return await self._destructive(spec, ctx, data, record, confirm)
        except Refused as refusal:
            await self.audit.single(record, "refused", refusal.code)
            raise
        return await self._run(spec, record, ctx, data)

    async def _destructive(
        self,
        spec: ActionSpec[Any, Any],
        ctx: PluginActionContext,
        data: BaseModel,
        record: CallRecord,
        confirm: str | None,
    ) -> BaseModel:
        if self.plans is None:
            raise RuntimeError("destructive actions need the core's plans")
        return await self.plans.handle(spec, ctx, data, record, confirm)

    async def run_apply(
        self,
        call: Call,
        audit_id: int,
        report: Callable[[Mapping[str, Any]], Awaitable[None]],
        diff: Diff | None = None,
    ) -> None:
        """The `apply` job: check the member again, then run the confirmed action and record its outcome.
        A refusal ends it quietly; a failure is recorded and raised, so the job fails and is not retried."""
        spec = self.actions[call.action]
        guild = self.client.get_guild(call.guild_id)
        try:
            if guild is None:
                raise Refused("not_in_guild", "the bot is not in that server")
            ctx, data = await self.prepare(spec, call, guild)
        except Refused as refusal:
            await self.audit.finished(audit_id, "refused", refusal.code)
            return
        try:
            result = await spec.run(replace(ctx, reporter=report, diff=diff, audit_id=audit_id), data)
        except Exception as exc:
            await self.audit.finished(audit_id, "error", repr(exc))
            raise
        await self.audit.finished(audit_id, "handed_off" if _handed_off(result) else "ok", None)

    async def prepare(
        self, spec: ActionSpec[Any, Any], call: Call, guild: Guild, *, check_input: bool = True
    ) -> tuple[PluginActionContext, BaseModel]:
        refusal = door_refusal(spec, call.actor.via, call.token)
        if refusal is not None:
            raise refusal
        plugin_id = spec.plugin_id
        source = self.sources[plugin_id]
        if not source.builtin and not await self.pools.core_db().fetchval(ENABLED, call.guild_id, plugin_id):
            raise Refused("plugin_not_enabled", f"{plugin_id} is not enabled in this server")
        try:
            config = await self.config.load(call.guild_id, plugin_id, source.config)
        except ConfigError as exc:
            raise Refused("config_invalid", str(exc)) from exc
        try:
            data = spec.input.model_validate(call.raw_input) if check_input else spec.input.model_construct()
        except ValidationError as exc:
            raise Refused("invalid_input", str(exc)) from exc
        await self._check_permission(spec, call, guild)
        open_db = (
            None
            if source.builtin
            else partial(self.pools.guild_plugin_db, plugin_id, call.guild_id, call.actor.user_id)
        )
        open_voice = (
            None
            if source.builtin or self.voice is None
            else partial(self.voice.bound, plugin_id, call.guild_id)
        )
        open_members = (
            None
            if source.builtin
            else partial(GuildMembers, self.pools.core_db(), call.guild_id, self.labels)
        )
        open_labels = (
            None
            if source.builtin or self.labels is None
            else partial(self.labels.bound, plugin_id, call.guild_id)
        )
        context = PluginActionContext(
            guild,
            call.actor,
            config,
            open_db,
            open_voice=open_voice,
            open_members=open_members,
            open_labels=open_labels,
        )
        return context, data

    async def suggest(self, call: Call, option: str, typed: str) -> list[Choice[Any]]:
        """Suggestions for one option of an action, as a member types it. `call.raw_input` holds the options filled
        in so far, which the suggester reads as `ctx.filled`. Nothing is audited, and anything that would refuse
        the action, or a suggester that fails or takes past Discord's three seconds, suggests nothing."""
        spec = self.actions.get(call.action)
        suggester = spec.suggest.get(option) if spec is not None else None
        guild = self.client.get_guild(call.guild_id)
        if spec is None or suggester is None or guild is None:
            return []
        try:
            ctx, _ = await self.prepare(spec, call, guild, check_input=False)
            ctx = replace(ctx, filled=dict(call.raw_input))
            return await asyncio.wait_for(suggester(ctx, typed), SUGGEST_SECONDS)
        except Refused:
            return []
        except Exception:
            log.exception("suggestions failed", extra={"action": call.action, "option": option})
            return []

    async def _check_permission(self, spec: ActionSpec[Any, Any], call: Call, guild: Guild) -> None:
        """The member's current permissions against the action's (D19)."""
        if not await member_permissions(guild, call.actor.user_id) >= spec.default_permissions:
            raise Refused("forbidden", f"{spec.name} needs permissions you do not have")

    async def _run(
        self, spec: ActionSpec[Any, Any], record: CallRecord, ctx: PluginActionContext, data: BaseModel
    ) -> BaseModel:
        audit_id = await self.audit.started(record) if spec.risk > Risk.READ else None
        started = time.monotonic()
        try:
            async with asyncio.timeout(spec.timeout.total_seconds()):
                output = await spec.run(ctx, data)
        except TimeoutError as exc:
            message = (
                f"{spec.name} took longer than {spec.timeout.total_seconds():g} seconds, so it was stopped."
            )
            await self._record(spec, record, audit_id, "error", message)
            raise ActionFailed(message, public_message="That took too long, so it was stopped.") from exc
        except ActionError as exc:
            await self._record(spec, record, audit_id, "error", str(exc))
            raise ActionFailed(repr(exc), public_message=str(exc)) from exc
        except Exception as exc:
            await self._record(spec, record, audit_id, "error", repr(exc))
            raise ActionFailed(repr(exc)) from exc
        await self._record(spec, record, audit_id, "ok", None)
        log.info(
            "action done",
            extra={"action": spec.name, "duration_ms": round((time.monotonic() - started) * 1000)},
        )
        return output

    async def _record(
        self,
        spec: ActionSpec[Any, Any],
        record: CallRecord,
        audit_id: int | None,
        outcome: str,
        error: str | None,
    ) -> None:
        if audit_id is None:
            await self.audit.single(record, outcome, error)
        else:
            await self.audit.finished(audit_id, outcome, error)
