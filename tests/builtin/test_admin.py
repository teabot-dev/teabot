"""The core admin plugin (D3, D10, D24): always enabled, Manage Server by default."""

import json
from typing import Any

import discord
import pytest
from pydantic import BaseModel, ConfigDict, Field

from teabot.adapters.schema import commands_for
from teabot.builtin.admin import (
    Admin,
    AuditEntry,
    LogChannelSet,
    PluginState,
    PluginStates,
    Recent,
    Settings,
    Toggled,
    render_log_channel,
    render_recent,
    render_settings,
    render_states,
    render_toggled,
    state_here,
)
from teabot.core.registry import ActionFailed, Call, Refused
from teabot.sdk import ActionContext, ActionSpec, Actor, Manifest, Registrar, Risk
from teabot.testing import FakeChannel, PluginHarness


class EchoConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prefix: str = Field(default=">", max_length=3)
    repeat: int = 1


class Say(BaseModel):
    text: str


class Said(BaseModel):
    text: str


async def say(ctx: ActionContext, data: Say) -> Said:
    config = ctx.config
    assert isinstance(config, EchoConfig)
    return Said(text=f"{config.prefix}{data.text * config.repeat}")


class Echo:
    manifest = Manifest("echo", "1.0", config=EchoConfig)

    def setup(self, registrar: Registrar) -> None:
        registrar.action(ActionSpec("echo.say", "Say something", Say, Said, Risk.READ, say))


@pytest.fixture
async def harness(teabot_harness: Any) -> PluginHarness:
    return await teabot_harness(Echo())


def text(out: BaseModel) -> str:
    return json.dumps(out.model_dump(mode="json"))


async def test_plugins_lists_each_installed_plugins_state_here(harness: PluginHarness) -> None:
    assert '"echo"' in text(await harness.invoke("plugins.list")) and '"enabled"' in text(
        await harness.invoke("plugins.list")
    )
    await harness.invoke("plugins.disable", {"plugin": "echo"})
    assert '"disabled"' in text(await harness.invoke("plugins.list"))


async def test_disabling_makes_the_plugins_actions_refuse_and_enabling_restores_them(
    harness: PluginHarness,
) -> None:
    await harness.invoke("plugins.disable", {"plugin": "echo"})
    with pytest.raises(Refused) as caught:
        await harness.invoke("echo.say", {"text": "hi"})
    assert caught.value.code == "plugin_not_enabled"
    await harness.invoke("plugins.enable", {"plugin": "echo"})
    assert await harness.invoke("echo.say", {"text": "hi"}) == Said(text=">hi")


@pytest.mark.parametrize("action", ["plugins.enable", "plugins.disable"])
async def test_an_unknown_plugin_is_told_to_the_member(harness: PluginHarness, action: str) -> None:
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke(action, {"plugin": "nope"})
    assert caught.value.public_message == "There is no installed plugin called nope."


async def test_a_plugin_that_is_not_loaded_cannot_be_enabled(harness: PluginHarness) -> None:
    harness.statuses["broken"] = "failed"
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("plugins.enable", {"plugin": "broken"})
    assert caught.value.public_message == "broken is not loaded (failed), so it cannot be enabled."


async def test_admin_itself_cannot_be_disabled(harness: PluginHarness) -> None:
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("plugins.disable", {"plugin": "admin"})
    assert caught.value.public_message == "There is no installed plugin called admin."


async def test_config_shows_a_plugins_settings(harness: PluginHarness) -> None:
    assert json.loads(text(await harness.invoke("plugins.config", {"plugin": "echo"}))) == {
        "plugin": "echo",
        "settings": {"prefix": ">", "repeat": 1},
    }


async def test_configure_validates_through_the_plugins_model(harness: PluginHarness) -> None:
    await harness.invoke("plugins.config", {"plugin": "echo", "setting": "repeat", "value": "2"})
    await harness.invoke("plugins.config", {"plugin": "echo", "setting": "prefix", "value": "!"})
    assert await harness.invoke("echo.say", {"text": "hi"}) == Said(text="!hihi")


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("colour", "red", "echo has no setting colour. Its settings are: prefix, repeat."),
        ("prefix", "toolong", "That value does not fit prefix:"),
        ("repeat", "lots", "That value does not fit repeat:"),
    ],
)
async def test_configure_refuses_unknown_settings_and_bad_values(
    harness: PluginHarness, setting: str, value: str, message: str
) -> None:
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("plugins.config", {"plugin": "echo", "setting": setting, "value": value})
    assert (caught.value.public_message or "").startswith(message)


async def test_log_channel_is_where_audit_posts_go(harness: PluginHarness) -> None:
    channel = harness.guild.add_channel(FakeChannel())
    await harness.invoke("audit.channel", {"channel": str(channel.id)})
    stored = await harness.admin.fetchval(
        "SELECT config->>'log_channel_id' FROM core.guild_plugins WHERE plugin_id = 'admin' AND guild_id = $1",
        harness.guild.id,
    )
    assert stored == str(channel.id)


async def test_recent_shows_this_servers_latest_changes(harness: PluginHarness) -> None:
    await harness.invoke("plugins.disable", {"plugin": "echo"})
    out = text(await harness.invoke("audit.recent"))
    assert "plugins.disable" in out and "ok" in out


@pytest.mark.parametrize("action", ["plugins.list", "plugins.config", "audit.recent"])
async def test_admin_needs_manage_server(harness: PluginHarness, action: str) -> None:
    member = harness.guild.add_member(discord.Permissions(manage_messages=True))
    raw = {"plugin": "echo"} if action == "plugins.config" else {}
    with pytest.raises(Refused) as caught:
        await harness.invoke(action, raw, user=member.id)
    assert caught.value.code == "forbidden"


async def test_a_plugin_that_is_not_loaded_has_no_settings(harness: PluginHarness) -> None:
    harness.statuses["broken"] = "failed"
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("plugins.config", {"plugin": "broken"})
    assert (
        caught.value.public_message
        == "broken is not loaded (failed), so it has no settings to show or change."
    )


async def test_admins_own_settings_go_through_log_channel_only(harness: PluginHarness) -> None:
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("plugins.config", {"plugin": "admin"})
    assert caught.value.public_message == "There is no installed plugin called admin."


@pytest.mark.parametrize(
    ("label", "enabled", "state"),
    [("loaded", True, "enabled"), ("loaded", False, "disabled"), ("failed", True, "not loaded (failed)")],
)
def test_state_here(label: str, enabled: bool, state: str) -> None:
    assert state_here(label, enabled) == state


def test_renders_read_as_sentences() -> None:
    assert (
        render_states(PluginStates(plugins=[PluginState(plugin="echo", state="enabled")])) == "`echo` enabled"
    )
    assert render_states(PluginStates(plugins=[])) == "No plugins are installed."
    assert render_toggled(Toggled(plugin="echo", enabled=False)) == "`echo` is now disabled here."
    assert render_toggled(Toggled(plugin="echo", enabled=True)) == "`echo` is now enabled here."
    assert (
        render_settings(Settings(plugin="echo", settings={"a": 1}))
        == '`echo` settings:\n```json\n{\n  "a": 1\n}\n```'
    )
    assert render_log_channel(LogChannelSet(channel_id=5)) == "Audit posts now go to <#5>."
    entry = AuditEntry(id=3, action="plugins.enable", outcome="ok", user_id=7)
    assert render_recent(Recent(entries=[entry])) == "`#3` plugins.enable ok by <@7>"
    assert render_recent(Recent(entries=[])) == "Nothing has been audited here yet."


async def test_a_newly_installed_plugin_starts_disabled_here(harness: PluginHarness) -> None:
    harness.statuses["fresh"] = "loaded"
    states = (await harness.invoke("plugins.list")).model_dump()["plugins"]
    assert {"plugin": "fresh", "state": "disabled"} in states


async def test_a_setting_without_a_value_is_told_to_the_member(harness: PluginHarness) -> None:
    with pytest.raises(ActionFailed) as caught:
        await harness.invoke("plugins.config", {"plugin": "echo", "setting": "repeat"})
    assert caught.value.public_message == "Give a value for repeat."


def test_admin_commands_are_the_plugins_and_audit_groups(harness: PluginHarness) -> None:
    admin = Admin(harness.pools.core_db(), statuses={}, configs={})
    names = {c["name"]: sorted(o["name"] for o in c["options"]) for c in commands_for(admin.sources())}
    assert names == {"plugins": ["config", "disable", "enable", "list"], "audit": ["channel", "recent"]}


@pytest.mark.parametrize("action", ["plugins.enable", "plugins.disable"])
async def test_enabling_or_disabling_queues_a_command_sync_here(harness: PluginHarness, action: str) -> None:
    await harness.invoke(action, {"plugin": "echo"})
    queued = await harness.admin.fetch("SELECT guild_id FROM core.jobs WHERE handler = 'sync_commands'")
    assert [r["guild_id"] for r in queued] == [harness.guild.id]


async def suggested(harness: PluginHarness, action: str, option: str, typed: str = "") -> list[Any]:
    choices = await harness.registry.suggest(
        Call(action, harness.guild.id, Actor(harness.member.id, "slash"), {}), option, typed
    )
    return [c.value for c in choices]


async def test_enable_suggests_the_plugins_that_are_off_and_disable_those_that_are_on(
    harness: PluginHarness,
) -> None:
    assert await suggested(harness, "plugins.disable", "plugin") == ["echo"]
    assert await suggested(harness, "plugins.enable", "plugin") == []
    await harness.invoke("plugins.disable", {"plugin": "echo"})
    assert await suggested(harness, "plugins.enable", "plugin", "ec") == ["echo"]
    assert await suggested(harness, "plugins.enable", "plugin", "zz") == []


async def test_config_suggests_plugins_and_their_settings(harness: PluginHarness) -> None:
    assert await suggested(harness, "plugins.config", "plugin") == ["echo"]
    assert await suggested(harness, "plugins.config", "setting", "rep") == ["repeat"]
