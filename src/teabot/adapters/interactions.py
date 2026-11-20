"""The interaction router (D29, D32): one handler for every teabot command and modal.

A command with a `LongText` field saves its other options as a draft row and opens a modal whose custom id
holds only the draft id, so any process can finish the submit (D40). Replies are ephemeral unless the action
asks for a public one; refusals say why, failures point at the audit log without leaking details.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

import discord
from pydantic import BaseModel

from teabot.adapters.schema import MESSAGE, USER, field_of_option, fold_lists, slash_fields
from teabot.core.plans import Planned, Queued
from teabot.core.registry import ActionFailed, Call, Refused
from teabot.db.pool import Database
from teabot.sdk import ActionSpec, Actor, Choice
from teabot.sdk._render import fields_shown
from teabot.sdk.actions import Via
from teabot.sdk.components import PREFIX as BUTTON_PREFIX
from teabot.sdk.components import SELECT_PREFIX, parse_custom_id, parse_select_id
from teabot.sdk.fields import LONG_TEXT_MAX
from teabot.sdk.inputs import InputField, asked, input_fields

log = logging.getLogger(__name__)

MODAL_PREFIX = "teabot:core:modal:"
EXPIRED = "That form has expired. Run the command again."
OUTSIDE = "teabot commands work inside a server."
FAILED = "That did not work. The error is in the audit log."
DRAFT_MINUTES = 15
FIELD_SHOWN = 1024

SAVE_DRAFT = """
INSERT INTO core.interaction_drafts (id, guild_id, user_id, action, input, via)
VALUES ($1, $2, $3, $4, $5::jsonb, $6)
"""
TAKE_DRAFT = """
DELETE FROM core.interaction_drafts
WHERE id = $1 AND guild_id = $2 AND user_id = $3 AND created_at > now() - make_interval(mins => $4)
RETURNING action, input, via
"""


class Invoker(Protocol):
    @property
    def actions(self) -> Mapping[str, ActionSpec[Any, Any]]: ...

    async def invoke(self, call: Call) -> BaseModel: ...

    async def suggest(self, call: Call, option: str, typed: str) -> list[Choice[Any]]: ...


def default_embed(spec: ActionSpec[Any, Any], output: BaseModel) -> discord.Embed:
    """An embed with one field per output field, for actions without their own renderer (D29). Values show by
    their type: times as Discord timestamps, IDs as mentions."""
    embed = discord.Embed(title=spec.summary)
    for name, text in fields_shown(output):
        embed.add_field(name=name.replace("_", " ").capitalize(), value=text[:FIELD_SHOWN])
    return embed


def _modal(spec: ActionSpec[Any, Any], draft_id: uuid.UUID, fields: list[InputField]) -> discord.ui.Modal:
    """A form for `fields`: long text in a paragraph box, plain text in a line."""
    modal = discord.ui.Modal(title=spec.summary[:45], custom_id=f"{MODAL_PREFIX}{draft_id}")
    for f in fields:
        long = f.kind == "long"
        modal.add_item(
            discord.ui.TextInput(
                label=f.description[:45],
                custom_id=f.name,
                style=discord.TextStyle.paragraph if long else discord.TextStyle.short,
                max_length=LONG_TEXT_MAX if long else min(f.max_length or LONG_TEXT_MAX, LONG_TEXT_MAX),
                required=f.required,
            )
        )
    return modal


def _modal_values(data: Mapping[str, Any]) -> dict[str, str]:
    return {
        item["custom_id"]: item["value"] for row in data.get("components", []) for item in row["components"]
    }


CONFIRM_PREFIX = "teabot:core:confirm:"
PLAN_GONE = "That plan no longer exists. Run the command again for a new one."
BUTTON_GONE = "That button no longer works."
MENU_GONE = "That command is no longer here. Discord can take a minute to update its menus."
# Discord lets a private reply be edited for 15 minutes; following stops a little before.
FOLLOW_SECONDS = 14 * 60
JOB_STATE = "SELECT state, progress FROM core.jobs WHERE id = $1"
ENDED = frozenset({"done", "failed", "cancelled"})


def _progress(row: Any) -> dict[str, Any] | None:
    if row is None or row["progress"] is None:
        return None
    value = row["progress"]
    return json.loads(value) if isinstance(value, str) else dict(value)


def progress_text(state: str, progress: Mapping[str, Any] | None, job_id: int, *, late: bool = False) -> str:
    """A confirmed change's reply, as its job goes: waiting, how far it has got, and how it ended."""
    so_far = ""
    if progress:
        if {"done", "total"} <= set(progress):
            so_far = f"{progress['done']} of {progress['total']}"
        else:
            so_far = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in progress.items())
    if state == "done":
        return f"Done. {so_far}." if so_far else "Done."
    if state == "failed":
        return (
            f"That did not finish. The error is in the audit log, and in `/admin job_status job_id:{job_id}`."
        )
    if state == "cancelled":
        return "Cancelled."
    if late:
        return f"Still applying. Follow it with `/admin job_status job_id:{job_id}`."
    if state == "running":
        return f"Applying: {so_far}." if so_far else "Applying."
    return "Applying: about to start."


PLAN_SHOWN = 10
PLANNED = "SELECT action, input FROM core.audit_log WHERE confirm = $1 AND outcome = 'planned'"


def plan_embed(plan: Planned) -> discord.Embed:
    """A destructive command's diff (D21). Entry targets are member-written, so their markdown is escaped."""
    if plan.stale:
        note = "The target has changed since your last plan, so nothing was applied. This is the fresh plan."
    else:
        note = "Nothing has changed yet. Confirm within 15 minutes to apply it."
    if plan.notes:
        note = "\n".join(discord.utils.escape_markdown(n) for n in plan.notes) + "\n\n" + note
    embed = discord.Embed(title=f"Plan: {plan.action}", description=note, colour=discord.Colour.orange())
    embed.add_field(name="Changes", value=", ".join(f"{k}: {v}" for k, v in plan.counts.items()) or "none")
    lines = [
        f"{e.change} {discord.utils.escape_markdown(e.target)}"
        + (f": {discord.utils.escape_markdown(e.detail)}" if e.detail else "")
        for e in plan.entries[:PLAN_SHOWN]
    ]
    if plan.total > len(lines):
        lines.append(f"and {plan.total - len(lines)} more")
    embed.add_field(name="First entries", value="\n".join(lines) or "none", inline=False)
    return embed


def confirm_view(plan: Planned) -> discord.ui.View:
    """One Confirm button. Its custom id holds the confirm value alone, so any process can act on it (D40)."""
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.danger, custom_id=f"{CONFIRM_PREFIX}{plan.confirm}"
        )
    )
    return view


class InteractionRouter:
    def __init__(
        self,
        registry: Invoker,
        db: Database,
        *,
        poll_seconds: float = 2.0,
        follow_seconds: float = FOLLOW_SECONDS,
    ) -> None:
        self.registry = registry
        self.db = db
        self.poll_seconds = poll_seconds
        self.follow_seconds = follow_seconds
        self.following: set[asyncio.Task[None]] = set()

    async def handle(self, interaction: Any) -> None:
        if interaction.type is discord.InteractionType.application_command:
            await self._command(interaction)
        elif interaction.type is discord.InteractionType.autocomplete:
            await self._autocomplete(interaction)
        elif interaction.type is discord.InteractionType.modal_submit and str(
            interaction.data.get("custom_id", "")
        ).startswith(MODAL_PREFIX):
            await self._modal_submit(interaction)
        elif interaction.type is discord.InteractionType.component and str(
            interaction.data.get("custom_id", "")
        ).startswith(CONFIRM_PREFIX):
            await self._confirm(interaction)
        elif interaction.type is discord.InteractionType.component and str(
            interaction.data.get("custom_id", "")
        ).startswith(BUTTON_PREFIX):
            await self._press(interaction)
        elif interaction.type is discord.InteractionType.component and str(
            interaction.data.get("custom_id", "")
        ).startswith(SELECT_PREFIX):
            await self._choose(interaction)

    async def _choose(self, interaction: Any) -> None:
        """A plugin's select menu: the bound call, with what the member chose filling its `into` parameter."""
        parsed = parse_select_id(str(interaction.data["custom_id"]))
        if parsed is None or interaction.guild_id is None:
            await interaction.response.send_message(BUTTON_GONE, ephemeral=True)
            return
        bound, into = parsed
        raw = dict(bound.input) | {into: list(interaction.data.get("values", []))}
        await self._run(interaction, bound.action, raw, via="component")

    async def _press(self, interaction: Any) -> None:
        """A plugin's button: the action and input its custom id holds, run as the member who pressed it,
        through the same call path as a slash command."""
        bound = parse_custom_id(str(interaction.data["custom_id"]))
        if bound is None or interaction.guild_id is None:
            await interaction.response.send_message(BUTTON_GONE, ephemeral=True)
            return
        spec = self.registry.actions.get(bound.action)
        fields = asked(spec.input, bound.input) if spec is not None else []
        if spec is not None and fields:
            await self._ask(interaction, spec, dict(bound.input), fields, "component")
            return
        await self._run(interaction, bound.action, dict(bound.input), via="component")

    async def _confirm(self, interaction: Any) -> None:
        """A Confirm press calls the action again with its plan's input and confirm value, through the same
        checks as MCP and the service API: the presser must be the member who planned it (D21)."""
        confirm = interaction.data["custom_id"].removeprefix(CONFIRM_PREFIX)
        row = await self.db.fetchrow(PLANNED, confirm)
        if row is None:
            await interaction.response.send_message(PLAN_GONE, ephemeral=True)
            return
        raw = json.loads(row["input"]) | {"confirm": confirm}
        await self._run(interaction, row["action"], raw, via="component")

    async def _autocomplete(self, interaction: Any) -> None:
        """Suggestions for the option the member is typing in, from the action's suggester."""
        sub = interaction.data["options"][0]
        name = f"{interaction.data['name']}.{sub['name']}"
        focused = next((o for o in sub.get("options", []) if o.get("focused")), None)
        spec = self.registry.actions.get(name)
        choices: list[Choice[Any]] = []
        if focused is not None and spec is not None and interaction.guild_id is not None:
            option = field_of_option(spec.input, str(focused["name"]))
            typed = str(focused.get("value", ""))
            # The options filled in before this one, as the action takes them, so a suggester can depend on them.
            given = {str(o["name"]): o.get("value") for o in sub.get("options", []) if not o.get("focused")}
            filled = fold_lists(spec.input, given)
            call = Call(name, interaction.guild_id, Actor(interaction.user.id, "slash"), filled)
            choices = await self.registry.suggest(call, option, typed)
        shown = [discord.app_commands.Choice(name=c.name[:100], value=c.value) for c in choices[:25]]
        await interaction.response.autocomplete(shown)

    async def _command(self, interaction: Any) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(OUTSIDE, ephemeral=True)
            return
        if interaction.data.get("type") in (USER, MESSAGE):
            await self._menu(interaction)
            return
        sub = interaction.data["options"][0]
        name = f"{interaction.data['name']}.{sub['name']}"
        raw = {o["name"]: o["value"] for o in sub.get("options", [])}
        spec = self.registry.actions.get(name)
        if spec is not None:
            raw = fold_lists(spec.input, raw)
        modal_fields = slash_fields(spec.input).modal if spec is not None else ()
        if spec is not None and modal_fields:
            described = {f.name: f for f in input_fields(spec.input)}
            await self._ask(interaction, spec, raw, [described[n] for n in modal_fields], "slash")
            return
        await self._run(interaction, name, raw)

    async def _ask(
        self,
        interaction: Any,
        spec: ActionSpec[Any, Any],
        raw: dict[str, Any],
        fields: list[InputField],
        via: Via,
    ) -> None:
        """Save what the member gave as a draft, and open a form for the rest, which any process can finish."""
        draft_id = uuid.uuid4()
        await self.db.execute(
            SAVE_DRAFT, draft_id, interaction.guild_id, interaction.user.id, spec.name, json.dumps(raw), via
        )
        await interaction.response.send_modal(_modal(spec, draft_id, fields))

    async def _menu(self, interaction: Any) -> None:
        """A right-click command on a member or a message: the action whose menu has that name fills its one
        input with the target."""
        kind = "user" if interaction.data["type"] == USER else "message"
        spec = next(
            (
                s
                for s in self.registry.actions.values()
                if s.menu is not None and s.menu.kind == kind and s.menu.name == interaction.data["name"]
            ),
            None,
        )
        if spec is None or spec.menu is None:
            await interaction.response.send_message(MENU_GONE, ephemeral=True)
            return
        target = str(interaction.data["target_id"])
        value: Any = int(target)
        if kind == "message":
            found = interaction.data.get("resolved", {}).get("messages", {}).get(target, {})
            value = {
                "id": int(target),
                "channel_id": int(found.get("channel_id", 0)),
                "author_id": int(found.get("author", {}).get("id", 0)),
                "content": str(found.get("content", "")),
                "attachments": [
                    {
                        "filename": str(f.get("filename", "")),
                        "url": str(f.get("url", "")),
                        "content_type": str(f.get("content_type", "")),
                    }
                    for f in found.get("attachments", [])
                ],
            }
        await self._run(interaction, spec.name, {spec.menu.field: value}, via="menu")

    async def _modal_submit(self, interaction: Any) -> None:
        try:
            draft_id = uuid.UUID(interaction.data["custom_id"].removeprefix(MODAL_PREFIX))
        except ValueError:
            draft_id = None
        row = None
        if draft_id is not None:
            row = await self.db.fetchrow(
                TAKE_DRAFT, draft_id, interaction.guild_id, interaction.user.id, DRAFT_MINUTES
            )
        if row is None:
            await interaction.response.send_message(EXPIRED, ephemeral=True)
            return
        raw = json.loads(row["input"]) | _modal_values(interaction.data)
        await self._run(interaction, row["action"], raw, via=row["via"])

    async def _run(self, interaction: Any, name: str, raw: dict[str, Any], via: Via = "slash") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        call = Call(name, interaction.guild_id, Actor(interaction.user.id, via), raw)
        try:
            output = await self.registry.invoke(call)
        except Refused as refusal:
            await interaction.followup.send(refusal.message, ephemeral=True)
            return
        except ActionFailed as failure:
            await interaction.followup.send(failure.public_message or FAILED, ephemeral=True)
            return
        await self._reply(interaction, self.registry.actions[name], output)

    async def _follow(self, message: Any, job_id: int) -> None:
        """Keep a confirmed change's reply up to date with its job, until it ends or Discord stops letting a
        private reply be edited."""
        shown = progress_text("pending", None, job_id)
        deadline = asyncio.get_running_loop().time() + self.follow_seconds
        while True:
            row = await self.db.fetchrow(JOB_STATE, job_id)
            ended = row is None or row["state"] in ENDED
            late = asyncio.get_running_loop().time() >= deadline
            text = progress_text(
                row["state"] if row else "done", _progress(row), job_id, late=late and not ended
            )
            if text != shown:
                try:
                    await message.edit(content=text)
                except discord.HTTPException:
                    return
                shown = text
            if ended or late:
                return
            await asyncio.sleep(self.poll_seconds)

    async def _reply(self, interaction: Any, spec: ActionSpec[Any, Any], output: BaseModel) -> None:
        if isinstance(output, Planned):
            view = confirm_view(output)
            await interaction.followup.send(embed=plan_embed(output), view=view, ephemeral=True)
            # The press arrives through on_interaction like any other, so discord.py need not keep the view.
            view.stop()
            return
        if isinstance(output, Queued):
            message = await interaction.followup.send(
                progress_text("pending", None, output.job_id), ephemeral=True, wait=True
            )
            task = asyncio.create_task(self._follow(message, output.job_id))
            self.following.add(task)
            task.add_done_callback(self.following.discard)
            return
        rendered = spec.render(output) if spec.render is not None else default_embed(spec, output)
        private = spec.reply != "public"
        if not private:
            # The command was acknowledged privately, so a refusal stays private; a success is shown to the
            # channel as a follow-up, and the private placeholder goes.
            await interaction.delete_original_response()
        if isinstance(rendered, str):
            await interaction.followup.send(rendered, ephemeral=private)
        else:
            await interaction.followup.send(embed=rendered, ephemeral=private)


async def prune_drafts(db: Database, minutes: int = DRAFT_MINUTES) -> str:
    """Drop drafts nobody submitted. Run from the daily maintenance job."""
    return await db.execute(
        "DELETE FROM core.interaction_drafts WHERE created_at < now() - make_interval(mins => $1)", minutes
    )
