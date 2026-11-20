"""`/tokens create | list | revoke` (D16, D17, D20): any member for their own, Manage Server for others'."""

from datetime import UTC, datetime
from typing import Any

import discord
import pytest
from pydantic import BaseModel

from teabot.builtin.tokens import Created, TokenInfo, TokenList, render_created, render_list
from teabot.core.registry import ActionFailed, Call, Refused
from teabot.sdk import ActionContext, ActionSpec, Actor, Manifest, Registrar, Risk
from teabot.testing import PluginHarness


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


async def create(harness: PluginHarness, user: int | None = None, **raw: Any) -> Any:
    return await harness.invoke("tokens.create", {"label": "claude", **raw}, user=user)


async def test_create_shows_the_token_once_and_it_checks_as_the_member(harness: PluginHarness) -> None:
    created = await create(harness)
    grant = await harness.tokens.check(created.token)
    assert (grant.guild_id, grant.user_id, grant.limits.max_risk) == (
        harness.guild.id,
        harness.member.id,
        Risk.WRITE,
    )
    assert created.token not in str(await harness.invoke("tokens.list"))


async def test_create_takes_a_risk_a_lifetime_and_an_allow_list(harness: PluginHarness) -> None:
    created = await create(harness, max_risk="read", days=7, actions="echo.say, tokens.list")
    grant = await harness.tokens.check(created.token)
    assert grant.limits.max_risk is Risk.READ
    assert grant.limits.allowed == frozenset({"echo.say", "tokens.list"})


async def test_an_allow_list_naming_no_real_action_is_refused(harness: PluginHarness) -> None:
    with pytest.raises(ActionFailed) as caught:
        await create(harness, actions="echo.say, echo.shout")
    assert caught.value.public_message == "There is no action echo.shout."


async def test_any_member_may_create_and_list_their_own(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    await create(harness, user=member.id)
    listed = await harness.invoke("tokens.list", user=member.id)
    assert [t["user_id"] for t in listed.model_dump()["tokens"]] == [member.id]


async def test_listing_everyones_tokens_needs_manage_server(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    await create(harness)
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("tokens.list", {"everyone": True}, user=member.id)
    assert caught.value.public_message == "Listing everyone's tokens needs Manage Server."
    assert len((await harness.invoke("tokens.list", {"everyone": True})).model_dump()["tokens"]) == 1


async def test_a_member_revokes_their_own_token(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    created = await create(harness, user=member.id)
    await harness.invoke("tokens.revoke", {"token_id": created.token_id}, user=member.id)
    assert await harness.invoke("tokens.list", user=member.id) == type(await harness.invoke("tokens.list"))(
        tokens=[]
    )


async def test_revoking_someone_elses_token_needs_manage_server(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    other = harness.guild.add_member(discord.Permissions.none())
    created = await create(harness, user=member.id)
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("tokens.revoke", {"token_id": created.token_id}, user=other.id)
    assert caught.value.public_message == "Revoking someone else's token needs Manage Server."
    await harness.invoke("tokens.revoke", {"token_id": created.token_id})
    assert len((await harness.invoke("tokens.list", {"everyone": True})).model_dump()["tokens"]) == 0


async def test_revoking_an_unknown_token_is_told_to_the_member(harness: PluginHarness) -> None:
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("tokens.revoke", {"token_id": "nope"})
    assert caught.value.public_message == "There is no live token nope in this server."


async def test_tokens_cannot_be_managed_with_a_token(harness: PluginHarness) -> None:
    created = await create(harness)
    with pytest.raises(Refused) as caught:
        await harness.invoke_with_token(created.token, "tokens.create", {"label": "more"})
    assert caught.value.code == "wrong_surface"


def test_the_created_reply_shows_the_token_once_with_its_limits() -> None:
    out = Created(
        token="teabot_ab_cd",
        token_id="ab",
        max_risk="read",
        actions=["echo.say"],
        expires_at=datetime(2026, 12, 13, tzinfo=UTC),
    )
    text = render_created(out)
    assert "teabot_ab_cd" in text and "read" in text and "echo.say" in text and "2026-12-13" in text
    assert "read -rs TEABOT_TOKEN" in text
    assert 'teabot http://127.0.0.1:8780/mcp --header "Authorization: Bearer $TEABOT_TOKEN"' in text
    assert "works only on the machine running teabot" in text


def test_a_public_address_goes_in_the_paste_line_without_the_local_warning() -> None:
    out = Created(
        token="t",
        token_id="ab",
        max_risk="write",
        actions=[],
        expires_at=datetime(2026, 12, 13, tzinfo=UTC),
        mcp_url="https://bot.example.org/mcp",
        local=False,
    )
    text = render_created(out)
    assert "teabot https://bot.example.org/mcp --header" in text
    assert "the address is `https://bot.example.org/mcp`" in text
    assert "works only on" not in text


def test_the_list_reply_names_each_token_and_says_when_there_are_none() -> None:
    row = TokenInfo(
        token_id="ab",
        user_id=5,
        label="claude",
        max_risk="write",
        actions=[],
        expires_at=datetime(2026, 12, 13, tzinfo=UTC),
        last_used=None,
    )
    assert render_list(TokenList(tokens=[row])) == "`ab` claude by <@5>, up to write, until 2026-12-13"
    assert render_list(TokenList(tokens=[])) == "No live tokens."


async def test_revoke_suggests_the_members_own_tokens(harness: PluginHarness) -> None:
    member = harness.guild.add_member(discord.Permissions.none())
    theirs = await create(harness, user=member.id)
    await create(harness)
    choices = await harness.registry.suggest(
        Call("tokens.revoke", harness.guild.id, Actor(member.id, "slash"), {}), "token_id", "cla"
    )
    assert [(c.name, c.value) for c in choices] == [(f"claude ({theirs.token_id})", theirs.token_id)]
    everyone = await harness.registry.suggest(
        Call("tokens.revoke", harness.guild.id, Actor(harness.member.id, "slash"), {}), "token_id", ""
    )
    assert len(everyone) == 2
