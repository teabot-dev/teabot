"""The interaction router (D29, D32): slash commands, LongText modals, replies and refusals."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import discord
import pytest
from pydantic import BaseModel

from teabot.adapters.interactions import InteractionRouter, default_embed, progress_text
from teabot.core.plans import Planned, Queued, ShownEntry
from teabot.core.registry import ActionFailed, Call, Refused
from teabot.db.migrate import migrate
from teabot.db.naming import Naming
from teabot.db.pool import Pools
from teabot.db.types import Conn
from teabot.sdk import (
    ActionSpec,
    Actor,
    ChannelRef,
    Choice,
    LongText,
    ReceivedMessage,
    Risk,
    Surface,
    UntrustedText,
)
from teabot.sdk.actions import Menu
from teabot.sdk.components import BoundAction

GUILD, USER, CHANNEL = 1001, 2001, 3001


class Post(BaseModel):
    channel: ChannelRef
    title: str
    body: LongText


class Posted(BaseModel):
    message_id: int
    preview: UntrustedText


class Ping(BaseModel):
    pass


class Pong(BaseModel):
    ok: bool


async def never(_ctx: Any, _data: Any) -> Any:
    raise AssertionError("the registry fake runs actions")


POST = ActionSpec("announcements.post", "Post an announcement", Post, Posted, Risk.WRITE, never)
WIPE = ActionSpec("announcements.wipe", "Wipe old posts", Ping, Pong, Risk.DESTRUCTIVE, never, plan=never)
PING = ActionSpec(
    "announcements.ping", "Ping", Ping, Pong, Risk.READ, never, render=lambda out: f"pong {out.ok}"
)
SHOUT = ActionSpec(
    "announcements.shout", "Shout", Ping, Pong, Risk.READ, never, render=lambda out: "HEY", reply="public"
)


@dataclass
class FakeRegistry:
    actions: dict[str, ActionSpec[Any, Any]]
    calls: list[Call] = field(default_factory=list[Call])
    suggested: list[tuple[str, str, str, dict[str, Any]]] = field(
        default_factory=list[tuple[str, str, str, dict[str, Any]]]
    )
    result: BaseModel | Exception | None = None

    async def invoke(self, call: Call) -> BaseModel:
        self.calls.append(call)
        if isinstance(self.result, Exception):
            raise self.result
        assert self.result is not None
        return self.result

    async def suggest(self, call: Call, option: str, typed: str) -> list[Choice[Any]]:
        self.suggested.append((call.action, option, typed, dict(call.raw_input)))
        return [Choice(f"{typed} one", "1"), Choice(f"{typed} two", "2")]


@dataclass
class FakeResponse:
    events: list[tuple[Any, ...]]

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.events.append(("defer", ephemeral, thinking))

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.events.append(("modal", modal))

    async def send_message(self, content: str | None = None, *, ephemeral: bool = False, **_: Any) -> None:
        self.events.append(("message", content, ephemeral))

    async def autocomplete(self, choices: list[Any]) -> None:
        self.events.append(("autocomplete", choices))


@dataclass
class FakeFollowup:
    events: list[tuple[Any, ...]]

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        wait: bool = False,
    ) -> "FakeSent":
        self.events.append(("followup", content if embed is None else embed, ephemeral))
        if view is not None:
            self.events.append(("view", view))
        return FakeSent(self.events)


@dataclass
class FakeSent:
    """A follow-up the router sent, which it may edit later."""

    events: list[tuple[Any, ...]]

    async def edit(self, *, content: str | None = None) -> None:
        self.events.append(("edit", content))


@dataclass
class FakeUser:
    id: int


class FakeInteraction:
    def __init__(
        self, kind: discord.InteractionType, data: dict[str, Any], guild_id: int | None = GUILD
    ) -> None:
        self.type = kind
        self.data = data
        self.guild_id = guild_id
        self.user = FakeUser(USER)
        self.events: list[tuple[Any, ...]] = []
        self.response = FakeResponse(self.events)
        self.followup = FakeFollowup(self.events)

    async def delete_original_response(self) -> None:
        self.events.append(("delete",))


def command(sub: str, options: list[dict[str, Any]]) -> FakeInteraction:
    data = {"name": "announcements", "options": [{"name": sub, "type": 1, "options": options}]}
    return FakeInteraction(discord.InteractionType.application_command, data)


def modal_submit(custom_id: str, values: dict[str, str]) -> FakeInteraction:
    rows = [{"type": 1, "components": [{"type": 4, "custom_id": k, "value": v}]} for k, v in values.items()]
    return FakeInteraction(discord.InteractionType.modal_submit, {"custom_id": custom_id, "components": rows})


@pytest.fixture
async def router(admin: Conn, naming: Naming, pools: Pools) -> tuple[InteractionRouter, FakeRegistry]:
    await migrate(admin, naming, [])
    registry = FakeRegistry({POST.name: POST, PING.name: PING, WIPE.name: WIPE, SHOUT.name: SHOUT})
    return InteractionRouter(registry, pools.core_db()), registry


async def test_a_command_without_long_text_defers_runs_and_replies_through_its_renderer(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    registry.result = Pong(ok=True)
    interaction = command("ping", [])
    await r.handle(interaction)
    assert registry.calls == [Call("announcements.ping", GUILD, registry.calls[0].actor, {})]
    assert registry.calls[0].actor.user_id == USER and registry.calls[0].actor.via == "slash"
    assert interaction.events == [("defer", True, True), ("followup", "pong True", True)]


async def test_a_command_with_long_text_opens_a_modal_and_the_submit_runs_it(
    router: tuple[InteractionRouter, FakeRegistry], admin: Conn
) -> None:
    r, registry = router
    first = command(
        "post",
        [{"name": "channel", "type": 7, "value": str(CHANNEL)}, {"name": "title", "type": 3, "value": "Hi"}],
    )
    await r.handle(first)
    ((kind, modal),) = first.events
    assert kind == "modal" and [c.custom_id for c in modal.children] == ["body"]
    assert registry.calls == []
    registry.result = Posted(message_id=9, preview="Hi there")
    submit = modal_submit(modal.custom_id, {"body": "Line one\nLine two"})
    await r.handle(submit)
    (call,) = registry.calls
    assert call.action == "announcements.post"
    assert call.raw_input == {"channel": str(CHANNEL), "title": "Hi", "body": "Line one\nLine two"}
    assert submit.events[0] == ("defer", True, True)
    kind, embed, ephemeral = submit.events[1]
    assert (kind, ephemeral) == ("followup", True) and isinstance(embed, discord.Embed)
    assert await admin.fetchval("SELECT count(*) FROM core.interaction_drafts") == 0


async def test_a_modal_submitted_by_someone_else_is_refused(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    first = command(
        "post",
        [{"name": "channel", "type": 7, "value": str(CHANNEL)}, {"name": "title", "type": 3, "value": "Hi"}],
    )
    await r.handle(first)
    modal = first.events[0][1]
    submit = modal_submit(modal.custom_id, {"body": "x"})
    submit.user = FakeUser(USER + 1)
    await r.handle(submit)
    assert registry.calls == [] and submit.events == [
        ("message", "That form has expired. Run the command again.", True)
    ]


async def test_an_unknown_or_expired_draft_is_refused(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    r, registry = router
    submit = modal_submit("teabot:core:modal:00000000-0000-0000-0000-000000000000", {"body": "x"})
    await r.handle(submit)
    assert registry.calls == [] and submit.events == [
        ("message", "That form has expired. Run the command again.", True)
    ]


async def test_a_refusal_is_told_to_the_member_privately(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    registry.result = Refused("forbidden", "announcements.ping needs permissions you do not have")
    interaction = command("ping", [])
    await r.handle(interaction)
    assert interaction.events[-1] == (
        "followup",
        "announcements.ping needs permissions you do not have",
        True,
    )


async def test_a_failure_is_reported_without_its_details(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    registry.result = ActionFailed("RuntimeError('secret detail')")
    interaction = command("ping", [])
    await r.handle(interaction)
    assert interaction.events[-1] == ("followup", "That did not work. The error is in the audit log.", True)


async def test_commands_outside_a_server_are_refused(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    r, registry = router
    interaction = command("ping", [])
    interaction.guild_id = None
    await r.handle(interaction)
    assert registry.calls == [] and interaction.events == [
        ("message", "teabot commands work inside a server.", True)
    ]


async def test_interactions_that_are_not_teabots_are_ignored(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    other = modal_submit("someone-elses-modal", {"x": "y"})
    await r.handle(other)
    assert other.events == [] and registry.calls == []


def test_the_default_embed_shows_each_output_field() -> None:
    embed = default_embed(POST, Posted(message_id=9, preview="Hi there"))
    assert embed.title == "Post an announcement"
    assert [(f.name, f.value) for f in embed.fields] == [("Message id", "9"), ("Preview", "Hi there")]


async def test_an_action_error_is_shown_to_the_member(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    r, registry = router
    registry.result = ActionFailed("ActionError('no')", public_message="that channel is not a text channel")
    interaction = command("ping", [])
    await r.handle(interaction)
    assert interaction.events[-1] == ("followup", "that channel is not a text channel", True)


CONFIRM = "c" * 22


def a_plan(stale: bool = False, entries: int = 2) -> Planned:
    shown = [ShownEntry(change="remove", target=f"post *{n}*") for n in range(min(entries, 20))]
    return Planned(
        action="announcements.wipe",
        counts={"remove": entries},
        entries=shown,
        total=entries,
        confirm=CONFIRM,
        expires_at=datetime(2026, 11, 14, 12, 15, tzinfo=UTC),
        stale=stale,
    )


def press(custom_id: str) -> FakeInteraction:
    return FakeInteraction(discord.InteractionType.component, {"custom_id": custom_id, "component_type": 2})


async def planned_row(admin: Conn, raw: dict[str, Any]) -> None:
    await admin.execute(
        "INSERT INTO core.audit_log (guild_id, user_id, surface, action, input, risk, outcome, diff_hash, confirm)"
        " VALUES ($1, $2, 'slash', 'announcements.wipe', $3::jsonb, 3, 'planned', 'h', $4)",
        GUILD,
        USER,
        json.dumps(raw),
        CONFIRM,
    )


async def test_a_plan_entry_shows_its_detail(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    handler, registry = router
    plan = a_plan(entries=1)
    plan.entries[0] = ShownEntry(change="cancel", target="Discord Nitro", detail="4 entrants")
    registry.result = plan
    interaction = command("wipe", [{"name": "ok", "type": 5, "value": True}])
    await handler.handle(interaction)
    embed = next(e for e in interaction.events if e[0] == "followup")[1]
    assert {f.name: f.value for f in embed.fields}["First entries"] == "cancel Discord Nitro: 4 entrants"


async def test_a_plugins_button_runs_its_action_as_the_member_who_pressed_it(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    registry.result = Pong(ok=True)
    interaction = press(BoundAction("announcements.ping", {"loud": True}).custom_id())
    await r.handle(interaction)
    (call,) = registry.calls
    assert (call.action, call.raw_input, call.actor.user_id, call.actor.via) == (
        "announcements.ping",
        {"loud": True},
        USER,
        "component",
    )
    assert interaction.events == [("defer", True, True), ("followup", "pong True", True)]


async def test_a_plugins_select_menu_fills_its_parameter_with_the_choices(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    registry.result = Pong(ok=True)
    custom_id = BoundAction("announcements.ping", {"loud": True}).custom_id("tags")
    interaction = FakeInteraction(
        discord.InteractionType.component, {"custom_id": custom_id, "component_type": 3, "values": ["a", "b"]}
    )
    await r.handle(interaction)
    (call,) = registry.calls
    assert (call.raw_input, call.actor.via) == ({"loud": True, "tags": ["a", "b"]}, "component")


async def test_autocomplete_answers_with_the_actions_suggestions(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    data = {
        "name": "announcements",
        "options": [
            {
                "type": 1,
                "name": "ping",
                "options": [{"name": "tags", "value": "a"}, {"name": "loud", "value": "ye", "focused": True}],
            }
        ],
    }
    interaction = FakeInteraction(discord.InteractionType.autocomplete, data)
    await r.handle(interaction)
    assert registry.suggested == [("announcements.ping", "loud", "ye", {"tags": "a"})]
    ((kind, shown),) = interaction.events
    assert kind == "autocomplete" and [(c.name, c.value) for c in shown] == [("ye one", "1"), ("ye two", "2")]


async def test_a_plan_is_shown_with_a_confirm_button(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    handler, registry = router
    registry.result = a_plan()
    interaction = command("wipe", [{"name": "ok", "type": 5, "value": True}])
    await handler.handle(interaction)
    followup = next(e for e in interaction.events if e[0] == "followup")
    embed, private = followup[1], followup[2]
    assert private is True
    assert embed.title == "Plan: announcements.wipe"
    assert "Nothing has changed yet" in (embed.description or "")
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Changes"] == "remove: 2"
    assert [f.inline for f in embed.fields] == [True, False]
    assert fields["First entries"] == "remove post \\*0\\*\nremove post \\*1\\*"
    view = next(e[1] for e in interaction.events if e[0] == "view")
    (button,) = view.children
    assert (button.label, button.custom_id) == ("Confirm", f"teabot:core:confirm:{CONFIRM}")
    assert view.is_finished()


async def test_a_long_plan_says_how_many_entries_are_not_shown(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    handler, registry = router
    registry.result = a_plan(entries=35)
    interaction = command("wipe", [{"name": "ok", "type": 5, "value": True}])
    await handler.handle(interaction)
    embed = next(e[1] for e in interaction.events if e[0] == "followup")
    assert {f.name: f.value for f in embed.fields}["First entries"].endswith("and 25 more")


async def test_a_stale_plan_says_the_target_changed(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    handler, registry = router
    registry.result = a_plan(stale=True)
    interaction = command("wipe", [{"name": "ok", "type": 5, "value": True}])
    await handler.handle(interaction)
    embed = next(e[1] for e in interaction.events if e[0] == "followup")
    assert "changed since" in (embed.description or "")


async def test_pressing_confirm_calls_the_action_again_with_the_plans_input(
    router: tuple[InteractionRouter, FakeRegistry], admin: Conn
) -> None:
    handler, registry = router
    await planned_row(admin, {"ok": True})
    registry.result = Queued(job_id=7, audit_id=9)
    interaction = press(f"teabot:core:confirm:{CONFIRM}")
    await handler.handle(interaction)
    assert registry.calls == [
        Call("announcements.wipe", GUILD, Actor(USER, "component"), {"ok": True, "confirm": CONFIRM})
    ]
    assert interaction.events[0] == ("defer", True, True)
    assert interaction.events[1] == ("followup", "Applying: about to start.", True)


async def test_a_confirmed_changes_reply_follows_its_job_to_the_end(
    router: tuple[InteractionRouter, FakeRegistry], admin: Conn
) -> None:
    handler, registry = router
    handler.poll_seconds = 0.01
    job_id = await admin.fetchval(
        "INSERT INTO core.jobs (guild_id, plugin_id, handler, state) VALUES ($1, 'core', 'apply', 'running') "
        "RETURNING id",
        GUILD,
    )
    await planned_row(admin, {"ok": True})
    registry.result = Queued(job_id=job_id, audit_id=9)
    interaction = press(f"teabot:core:confirm:{CONFIRM}")
    await handler.handle(interaction)
    await admin.execute('UPDATE core.jobs SET progress = \'{"done": 1, "total": 2}\' WHERE id = $1', job_id)
    await asyncio.sleep(0.1)
    await admin.execute(
        "UPDATE core.jobs SET state = 'done', progress = '{\"done\": 2, \"total\": 2}' WHERE id = $1", job_id
    )
    await asyncio.gather(*handler.following)
    edits = [e[1] for e in interaction.events if e[0] == "edit"]
    assert edits[0] in ("Applying.", "Applying: 1 of 2.")
    assert edits[-1] == "Done. 2 of 2."


def test_progress_reads_plainly_at_each_stage() -> None:
    assert progress_text("running", {"cleared": 3}, 5) == "Applying: cleared 3."
    assert progress_text("failed", None, 5).startswith("That did not finish.")
    assert (
        progress_text("running", None, 5, late=True)
        == "Still applying. Follow it with `/admin job_status job_id:5`."
    )


async def test_a_button_for_a_plan_that_is_gone_is_refused(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    handler, registry = router
    interaction = press(f"teabot:core:confirm:{CONFIRM}")
    await handler.handle(interaction)
    assert registry.calls == []
    assert interaction.events == [
        ("message", "That plan no longer exists. Run the command again for a new one.", True)
    ]


async def test_a_refused_confirm_is_told_privately(
    router: tuple[InteractionRouter, FakeRegistry], admin: Conn
) -> None:
    handler, registry = router
    await planned_row(admin, {"ok": True})
    registry.result = Refused("confirm_mismatch", "that plan was made by someone else")
    interaction = press(f"teabot:core:confirm:{CONFIRM}")
    await handler.handle(interaction)
    assert interaction.events[-1] == ("followup", "that plan was made by someone else", True)


async def test_other_buttons_are_left_alone(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    handler, registry = router
    interaction = press("someone-elses-button")
    await handler.handle(interaction)
    assert (registry.calls, interaction.events) == ([], [])


async def test_a_public_action_replies_to_the_channel_once_it_succeeds(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    r, registry = router
    registry.result = Pong(ok=True)
    interaction = command("shout", [])
    await r.handle(interaction)
    assert interaction.events == [("defer", True, True), ("delete",), ("followup", "HEY", False)]


async def test_a_public_actions_refusal_stays_private(router: tuple[InteractionRouter, FakeRegistry]) -> None:
    r, registry = router
    registry.result = Refused("forbidden", "announcements.shout needs permissions you do not have")
    interaction = command("shout", [])
    await r.handle(interaction)
    assert interaction.events[-1] == (
        "followup",
        "announcements.shout needs permissions you do not have",
        True,
    )
    assert ("delete",) not in interaction.events


class Target(BaseModel):
    message: ReceivedMessage


REPORT = ActionSpec(
    "announcements.report",
    "Report a message",
    Target,
    Pong,
    Risk.WRITE,
    never,
    surfaces=Surface.MENU,
    menu=Menu("Report", "message", "message"),
)


async def test_a_message_menu_runs_its_action_with_the_message_through_the_menu_door(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    handler, registry = router
    registry.actions[REPORT.name] = REPORT
    registry.result = Pong(ok=True)
    data = {
        "type": 3,
        "name": "Report",
        "target_id": "77",
        "resolved": {
            "messages": {
                "77": {
                    "channel_id": "5",
                    "author": {"id": "9"},
                    "content": "spam",
                    "attachments": [
                        {"filename": "a.png", "url": "https://cdn.example/a.png", "content_type": "image/png"}
                    ],
                }
            }
        },
    }
    await handler.handle(FakeInteraction(discord.InteractionType.application_command, data))
    assert registry.calls == [
        Call(
            "announcements.report",
            GUILD,
            Actor(USER, "menu"),
            {
                "message": {
                    "id": 77,
                    "channel_id": 5,
                    "author_id": 9,
                    "content": "spam",
                    "attachments": [
                        {"filename": "a.png", "url": "https://cdn.example/a.png", "content_type": "image/png"}
                    ],
                }
            },
        )
    ]


async def test_a_menu_no_action_has_is_told_to_the_member(
    router: tuple[InteractionRouter, FakeRegistry],
) -> None:
    handler, registry = router
    interaction = FakeInteraction(
        discord.InteractionType.application_command, {"type": 2, "name": "Gone", "target_id": "1"}
    )
    await handler.handle(interaction)
    assert registry.calls == []
    assert "no longer here" in str(interaction.events[0])


async def test_a_button_that_leaves_text_out_asks_for_it_in_a_form(
    router: tuple[InteractionRouter, FakeRegistry], admin: Conn
) -> None:
    r, registry = router
    interaction = press(BoundAction("announcements.post", {"channel": str(CHANNEL)}).custom_id())
    await r.handle(interaction)
    ((kind, modal),) = interaction.events
    assert kind == "modal"
    styles = {c.custom_id: c.style for c in modal.children}
    assert styles == {"title": discord.TextStyle.short, "body": discord.TextStyle.paragraph}
    assert registry.calls == []
    registry.result = Posted(message_id=9, preview="Hi")
    await r.handle(modal_submit(modal.custom_id, {"title": "Hi", "body": "Text"}))
    (call,) = registry.calls
    assert call.raw_input == {"channel": str(CHANNEL), "title": "Hi", "body": "Text"}
    assert call.actor.via == "component"
