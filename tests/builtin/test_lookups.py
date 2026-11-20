"""The admin plugin's lookups (D25): `READ` actions on every surface, for finding ids and progress."""

from typing import Any

import discord
import pytest
from pydantic import BaseModel

from teabot.auth.tokens import NewToken
from teabot.core.registry import ActionFailed, Call, Refused
from teabot.sdk import ActionContext, ActionSpec, Actor, Manifest, Registrar, Risk
from teabot.testing import FakeCategory, FakeChannel, FakeVoiceChannel, PluginHarness


class NoInput(BaseModel):
    pass


async def nothing(ctx: ActionContext, data: NoInput) -> NoInput:
    return data


class Echo:
    manifest = Manifest("echo", "1.0")

    def setup(self, registrar: Registrar) -> None:
        registrar.action(ActionSpec("echo.say", "Say", NoInput, NoInput, Risk.WRITE, nothing))


@pytest.fixture
async def harness(teabot_harness: Any) -> PluginHarness:
    return await teabot_harness(Echo())


def dumped(out: BaseModel) -> dict[str, Any]:
    return out.model_dump(mode="json")


async def test_describe_guild_lists_what_the_member_can_see(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    general = harness.guild.add_channel(FakeChannel(name="general"))
    harness.guild.add_channel(FakeChannel(name="staff", hidden_from={member.id}))
    voice = harness.guild.add_channel(FakeVoiceChannel(name="lounge"))
    role = harness.guild.add_role("Captain")
    out = dumped(await harness.invoke("admin.describe_guild", user=member.id))
    assert out["channels"] == [
        {"id": str(general.id), "name": "general", "kind": "text", "category": None},
        {"id": str(voice.id), "name": "lounge", "kind": "voice", "category": None},
    ]
    assert out["roles"] == [{"id": str(role.id), "name": "Captain"}]
    assert (out["page"], out["pages"]) == (1, 1)


async def test_describe_guild_pages_long_lists(harness: PluginHarness) -> None:
    for n in range(60):
        harness.guild.add_channel(FakeChannel(name=f"c{n}"))
    first = dumped(await harness.invoke("admin.describe_guild"))
    second = dumped(await harness.invoke("admin.describe_guild", {"page": 2}))
    assert (len(first["channels"]), first["pages"], len(second["channels"])) == (50, 2, 10)
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("admin.describe_guild", {"page": 3})
    assert caught.value.public_message == "There are only 2 pages."


async def test_lookups_work_through_a_read_only_token(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none(), name="ann")
    issued = await harness.tokens.issue(harness.guild.id, member.id, NewToken("claude", max_risk=Risk.READ))
    out = dumped(await harness.invoke_with_token(issued.token, "admin.find_members", {"query": "an"}))
    assert [m["id"] for m in out["members"]] == [str(member.id)]
    assert (
        dumped(await harness.invoke_with_token(issued.token, "admin.describe_guild", via="service"))["page"]
        == 1
    )


async def test_find_members_searches_names_and_nicknames_cached_or_not(harness: PluginHarness) -> None:
    ann = harness.guild.add_member(discord.Permissions.none(), name="ann")
    bob = harness.guild.add_member(discord.Permissions.none(), name="bob", nick="Annie", cached=False)
    harness.guild.add_member(discord.Permissions.none(), name="carl")
    out = dumped(await harness.invoke("admin.find_members", {"query": "AN"}))
    assert out["members"] == [
        {"id": str(ann.id), "name": "ann", "display_name": "ann"},
        {"id": str(bob.id), "name": "bob", "display_name": "Annie"},
    ]


async def test_find_members_returns_at_most_the_limit_and_refuses_over_100(harness: PluginHarness) -> None:
    for n in range(12):
        harness.guild.add_member(discord.Permissions.none(), name=f"zed{n}")
    assert len(dumped(await harness.invoke("admin.find_members", {"query": "zed"}))["members"]) == 10
    assert (
        len(dumped(await harness.invoke("admin.find_members", {"query": "zed", "limit": 3}))["members"]) == 3
    )
    with pytest.raises(Refused) as caught:
        await harness.invoke("admin.find_members", {"query": "zed", "limit": 101})
    assert caught.value.code == "invalid_input"


async def job(harness: PluginHarness, *, guild_id: int | None = None, started_by: int | None = None) -> int:
    return await harness.admin.fetchval(
        "INSERT INTO core.jobs (guild_id, plugin_id, handler, started_by, progress, last_error)"
        " VALUES ($1, 'echo', 'draw', $2, '{\"done\": 3}', 'slow') RETURNING id",
        guild_id or harness.guild.id,
        started_by,
    )


async def test_job_status_shows_the_starters_own_job(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    job_id = await job(harness, started_by=member.id)
    out = dumped(await harness.invoke("admin.job_status", {"job_id": job_id}, user=member.id))
    assert {k: out[k] for k in ("id", "plugin", "handler", "state", "attempts", "progress", "error")} == {
        "id": job_id,
        "plugin": "echo",
        "handler": "draw",
        "state": "pending",
        "attempts": 0,
        "progress": {"done": 3},
        "error": "slow",
    }


async def test_someone_elses_job_needs_manage_server(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    job_id = await job(harness, started_by=harness.member.id)
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("admin.job_status", {"job_id": job_id}, user=member.id)
    assert caught.value.public_message == "Seeing someone else's job needs Manage Server."
    assert dumped(await harness.invoke("admin.job_status", {"job_id": job_id}))["id"] == job_id


@pytest.mark.parametrize("elsewhere", [True, False])
async def test_a_job_of_another_server_or_none_at_all_is_not_found(
    harness: PluginHarness, elsewhere: bool
) -> None:
    job_id = await job(harness, guild_id=9999) if elsewhere else 424242
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("admin.job_status", {"job_id": job_id})
    assert caught.value.public_message == f"There is no job {job_id} in this server."


async def test_categories_are_not_listed_but_name_their_channels(harness: PluginHarness) -> None:
    events = harness.guild.add_channel(FakeCategory(name="Events"))
    bingo = harness.guild.add_channel(FakeChannel(name="bingo", category=events))
    out = dumped(await harness.invoke("admin.describe_guild"))
    assert out["channels"] == [{"id": str(bingo.id), "name": "bingo", "kind": "text", "category": "Events"}]


async def test_a_job_that_has_not_reported_yet_has_no_progress(harness: PluginHarness) -> None:
    job_id = await job(harness)
    await harness.admin.execute("UPDATE core.jobs SET progress = NULL WHERE id = $1", job_id)
    assert dumped(await harness.invoke("admin.job_status", {"job_id": job_id}))["progress"] is None


async def test_job_status_names_what_the_job_does_and_who_started_it(harness: PluginHarness) -> None:
    job_id = await job(harness, started_by=harness.member.id)
    out = dumped(await harness.invoke("admin.job_status", {"job_id": job_id}))
    assert (out["label"], out["started_by"]) == ("echo.draw", str(harness.member.id))


async def test_job_status_suggests_the_members_own_jobs_newest_first(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    first, second = await job(harness, started_by=member.id), await job(harness, started_by=member.id)
    await job(harness, started_by=harness.member.id)
    choices = await harness.registry.suggest(
        Call("admin.job_status", harness.guild.id, Actor(member.id, "slash"), {}), "job_id", ""
    )
    assert [c.value for c in choices] == [second, first]
    assert choices[0].name == f"{second} echo.draw (pending)"
