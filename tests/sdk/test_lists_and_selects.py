"""List inputs, which Discord shows as numbered options and MCP as an array, and select menus, which fill one."""

from typing import Annotated

import discord
import pytest
from pydantic import Field

from teabot.adapters.schema import fold_lists, mcp_input_schema, slash_fields
from teabot.core.registry import Call
from teabot.sdk import Actor, Choice, Context, EmptyConfig, Option, Plugin, Role, Select, Surface, TextChannel
from teabot.sdk._signature import SignatureError
from teabot.sdk._views import _menu  # pyright: ignore[reportPrivateUsage]
from teabot.testing import FakeChannel, HarnessFactory

plugin = Plugin("lists")
type Ctx = Context[EmptyConfig]
type Steps = Annotated[list[Role], Field(max_length=25)]
type Picked = Annotated[list[Role], Field(min_length=1, max_length=3)]
type Tags = Annotated[list[str], Field(max_length=5)]


@plugin.action()
async def name_roles(ctx: Ctx, roles: Picked) -> str:
    """Name some roles.

    Args:
        roles: The roles
    """
    return ", ".join(r.name for r in roles)


@plugin.action()
async def menu(ctx: Ctx, channel: TextChannel) -> str:
    """Post a menu of tags and a role picker.

    Args:
        channel: Where to post
    """
    tags = Select(
        tag.bind(prefix="#"),
        into="chosen",
        options=(Option("Raids", "raids"), Option("Bossing", "boss")),
        max=2,
    )
    await channel.send("Tags", select=tags)
    await channel.send("Roles", select=Select(pick.bind(), into="roles", kind="role", max=3))
    return "posted"


@plugin.action()
async def tag(ctx: Ctx, prefix: str, chosen: Tags | None = None) -> str:
    """Show the chosen tags.

    Args:
        prefix: Put before each tag
        chosen: The tags chosen
    """
    return " ".join(prefix + c for c in chosen or [])


@plugin.action()
async def pick(ctx: Ctx, roles: Annotated[list[Role], Field(max_length=3)] | None = None) -> str:
    """Show the chosen roles.

    Args:
        roles: The roles chosen
    """
    return ", ".join(r.name for r in roles or []) or "none"


def test_a_list_is_numbered_options_on_discord_and_an_array_over_mcp() -> None:
    spec = plugin.actions["lists.name_roles"].spec()
    options = slash_fields(spec.input).options
    assert [(o["name"], o["type"], o["required"]) for o in options] == [
        ("roles_1", 8, True),
        ("roles_2", 8, False),
        ("roles_3", 8, False),
    ]
    assert mcp_input_schema(spec)["properties"]["roles"]["type"] == "array"
    assert fold_lists(spec.input, {"roles_1": "1", "roles_3": "3"}) == {"roles": ["1", "3"]}


async def test_a_list_of_roles_arrives_as_the_roles(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(plugin)
    raids, boss = harness.guild.add_role("Raids"), harness.guild.add_role("Bossing")
    assert await harness.call(name_roles, roles=[raids, boss]) == "Raids, Bossing"


async def test_a_menu_of_options_fills_its_list_with_the_values_chosen(
    teabot_harness: HarnessFactory,
) -> None:
    harness = await teabot_harness(plugin)
    channel = harness.guild.add_channel(FakeChannel())
    await harness.call(menu, channel=channel)
    select = channel.sent[0].select
    assert select is not None and (select.kind, select.options, select.max) == (
        "options",
        ["raids", "boss"],
        2,
    )
    member = harness.guild.add_member(discord.Permissions.none())
    assert await harness.as_member(member).choose(select, ["raids", "boss"]) == "#raids #boss"


async def test_a_role_picker_fills_its_list_with_the_roles_chosen(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(plugin)
    channel = harness.guild.add_channel(FakeChannel())
    raids = harness.guild.add_role("Raids")
    await harness.call(menu, channel=channel)
    select = channel.sent[1].select
    assert select is not None and select.kind == "role"
    assert await harness.choose(select, [raids]) == "Raids"
    assert await harness.choose(select, []) == "none"


async def test_a_picker_opens_with_its_defaults_chosen(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(plugin)
    channel = harness.guild.add_channel(FakeChannel())
    raids = harness.guild.add_role("Raids")
    message = await channel.send(
        "Roles", select=Select(pick.bind(), into="roles", kind="role", max=3, defaults=(raids.id,))
    )
    assert message.select is not None and message.select.defaults == [raids.id]


def test_defaults_belong_to_pickers_and_fit_within_max() -> None:
    with pytest.raises(ValueError, match="Option\\(default=True\\)"):
        Select(pick.bind(), into="roles", options=(Option("a", "a"),), defaults=(1,))
    with pytest.raises(ValueError, match="at most max=1"):
        Select(pick.bind(), into="roles", kind="role", defaults=(1, 2))


def test_a_list_without_a_limit_is_refused_when_the_action_is_set_up() -> None:
    bad = Plugin("bad")

    @bad.action()
    async def many(ctx: Ctx, roles: list[Role]) -> str:
        """Too many.

        Args:
            roles: The roles
        """
        return ""

    with pytest.raises((SignatureError, ValueError), match="a list needs Field"):
        bad.actions["bad.many"].spec()


def test_a_list_of_more_than_ten_is_for_actions_no_slash_command_offers() -> None:
    wide = Plugin("wide")

    @wide.action()
    async def slashed(ctx: Ctx, roles: Steps) -> str:
        """Too many for a slash command.

        Args:
            roles: The roles
        """
        return ""

    @wide.action(surfaces=Surface.MCP)
    async def tooled(ctx: Ctx, roles: Steps) -> str:
        """Fine for an AI assistant.

        Args:
            roles: The roles
        """
        return ""

    with pytest.raises(ValueError, match="a slash command's list holds at most 10"):
        wide.actions["wide.slashed"].spec()
    assert mcp_input_schema(wide.actions["wide.tooled"].spec())["properties"]["roles"]["maxItems"] == 25


picker = Plugin("suggest")
type PCtx = Context[EmptyConfig]


@picker.action(needs="manage_guild")
async def find(ctx: PCtx, name: str) -> str:
    """Find a thing.

    Args:
        name: The thing's name
    """
    return name


@find.autocomplete("name")
async def names(ctx: PCtx, typed: str) -> list[Choice[str]]:
    if typed == "boom":
        raise RuntimeError("the suggester broke")
    return [Choice(n, n) for n in ("raids", "bossing", "rares") if n.startswith(typed)]


async def test_an_option_suggests_values_as_the_member_types(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(picker)
    spec = picker.actions["suggest.find"].spec()
    assert slash_fields(spec.input, spec.suggest).options[0]["autocomplete"] is True
    assert [c.value for c in await harness.autocomplete(find, "name", "ra")] == ["raids", "rares"]
    assert await harness.autocomplete(find, "name", "boom") == []


async def test_a_member_who_may_not_run_the_action_gets_no_suggestions(
    teabot_harness: HarnessFactory,
) -> None:
    harness = await teabot_harness(picker)
    member = harness.guild.add_member(discord.Permissions.none())
    found = await harness.registry.suggest(
        Call("suggest.find", harness.guild.id, Actor(member.id, "slash"), {}), "name", "ra"
    )
    assert found == []


@picker.action()
async def pick_item(ctx: PCtx, kind: str, item: str) -> str:
    """Pick an item of a kind.

    Args:
        kind: The kind of item
        item: The item
    """
    return item


@pick_item.autocomplete("item")
async def items(ctx: PCtx, typed: str) -> list[Choice[str]]:
    kinds = {"fruit": ["apple", "pear"], "tool": ["axe", "pick"]}
    return [Choice(n, n) for n in kinds.get(str(ctx.filled.get("kind")), []) if n.startswith(typed)]


async def test_a_suggester_sees_the_options_filled_in_before_it(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(picker)
    assert [c.value for c in await harness.autocomplete(pick_item, "item", filled={"kind": "tool"})] == [
        "axe",
        "pick",
    ]
    assert [
        c.value for c in await harness.autocomplete(pick_item, "item", "p", filled={"kind": "fruit"})
    ] == ["pear"]
    assert await harness.autocomplete(pick_item, "item") == []


def test_suggestions_are_for_options_the_action_has() -> None:
    with pytest.raises(SignatureError, match="has no option nope"):
        find.autocomplete("nope")


async def test_discord_gets_the_defaults_as_its_default_values() -> None:
    item = _menu(Select(pick.bind(), into="roles", kind="role", max=2, defaults=(5, 6)))
    assert [int(d.id) for d in getattr(item, "default_values", [])] == [5, 6]
