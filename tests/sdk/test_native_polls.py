"""Discord's own polls through the SDK: a plugin sends one, pings roles with it, reads its counts, and ends it
early, and a deleted message is told apart from a channel the bot can no longer see."""

from datetime import timedelta

import discord
import pytest

from teabot.sdk import ActionError, MessageGone, MessageHidden, Plugin, Poll, PollAnswer, TextChannel
from teabot.sdk._views import guild_view
from teabot.testing import HarnessFactory
from teabot.testing.fakes import FakeChannel, FakeGuild, FakeMessage

plugin = Plugin("pollster")


def setup() -> tuple[FakeGuild, FakeChannel, TextChannel]:
    guild = FakeGuild()
    raw = guild.add_channel(FakeChannel(name="votes"))
    channel = guild_view(guild).get_text_channel(raw.id)
    assert channel is not None
    return guild, raw, channel


def poll(*answers: str, multiple: bool = False) -> Poll:
    return Poll("Which event next?", answers or ("Tea Maze", "Battleships"), timedelta(days=2), multiple)


def test_a_poll_is_checked_against_discord_s_limits_when_made() -> None:
    assert poll(" Tea Maze ", "Battleships").answers == ("Tea Maze", "Battleships")
    for bad, says in [
        (lambda: Poll("", ("a", "b")), "question"),
        (lambda: Poll("q" * 301, ("a", "b")), "question"),
        (lambda: Poll("q", ("a",)), "2 to 10"),
        (lambda: Poll("q", tuple(str(n) for n in range(11))), "2 to 10"),
        (lambda: Poll("q", ("a", "b" * 56)), "55"),
        (lambda: Poll("q", ("a", " ")), "55"),
        (lambda: Poll("q", ("Yes", "yes")), "once"),
        (lambda: Poll("q", ("a", "b"), timedelta(minutes=30)), "whole hours"),
        (lambda: Poll("q", ("a", "b"), timedelta(days=33)), "whole hours"),
        (lambda: Poll("q", ("a", "b"), timedelta(hours=1, minutes=5)), "whole hours"),
    ]:
        with pytest.raises(ValueError, match=says):
            bad()


async def test_a_sent_poll_is_read_with_its_counts_and_ends_when_discord_says() -> None:
    guild, raw, channel = setup()
    message = await channel.send("Vote below", poll=poll())
    [sent] = raw.sent
    assert sent.poll is not None and sent.content == "Vote below"
    assert message.poll_ends_at == sent.poll.expires_at
    sam, kim = guild.add_member(discord.Permissions.none()), guild.add_member(discord.Permissions.none())
    sent.poll.vote(sam.id, "Tea Maze")
    sent.poll.vote(kim.id, "Tea Maze")
    sent.poll.vote(kim.id, "Battleships")
    read = await channel.read_poll(message.id)
    assert read is not None
    assert (read.question, read.multiple, read.final, read.votes) == ("Which event next?", False, False, 2)
    assert read.answers == (PollAnswer(1, "Tea Maze", 1), PollAnswer(2, "Battleships", 1))
    await channel.end_poll(message.id)
    ended = await channel.read_poll(message.id)
    assert ended is not None and ended.final
    await channel.end_poll(message.id)


async def test_a_multiple_choice_poll_counts_every_vote() -> None:
    guild, raw, channel = setup()
    message = await channel.send(poll=poll(multiple=True))
    sam = guild.add_member(discord.Permissions.none())
    poll_on = raw.sent[0].poll
    assert poll_on is not None
    poll_on.vote(sam.id, "Tea Maze")
    poll_on.vote(sam.id, "Battleships")
    read = await channel.read_poll(message.id)
    assert read is not None and read.multiple and read.votes == 2


async def test_a_deleted_message_is_gone_and_a_hidden_channel_says_so() -> None:
    guild, raw, channel = setup()
    message = await channel.send(poll=poll())
    plain = await channel.send("no poll")
    assert await channel.read_poll(plain.id) is None
    raw.hidden_from.add(guild.me.id)
    with pytest.raises(MessageHidden, match="can no longer see"):
        await channel.read_poll(message.id)
    raw.hidden_from.clear()
    await message.delete()
    assert await channel.read_poll(message.id) is None
    with pytest.raises(MessageGone):
        await channel.end_poll(message.id)


async def test_sending_a_poll_names_the_permissions_the_bot_lacks() -> None:
    guild, raw, channel = setup()
    guild.me.guild_permissions = discord.Permissions(view_channel=True, send_messages=True)
    with pytest.raises(ActionError, match="I need Read Message History and Send Polls in"):
        await channel.send(poll=poll())
    guild.me.guild_permissions = discord.Permissions(administrator=True)
    raw.refused.add("send_polls")
    with pytest.raises(ActionError, match="I need Send Polls in"):
        await channel.send(poll=poll())
    assert raw.sent == []


async def test_a_role_ping_is_written_into_the_message_and_allowed_alone() -> None:
    guild, raw, channel = setup()
    events = guild.add_role("events")
    await channel.send("A poll", poll=poll(), ping_roles=[events.id])
    [sent] = raw.sent
    assert sent.content == f"<@&{events.id}>\nA poll"
    assert sent.allowed_mentions is not None
    roles = sent.allowed_mentions.roles
    assert isinstance(roles, list) and [r.id for r in roles] == [events.id]


async def test_can_mention_says_who_lacks_mention_everyone() -> None:
    guild, raw, channel = setup()
    events = guild.add_role("events")
    view = guild_view(guild)
    role = view.get_role(events.id)
    member = guild.add_member(discord.Permissions.none())
    asker = await view.get_member(member.id)
    assert role is not None and asker is not None
    assert channel.can_mention(role) is None
    assert "you need Mention Everyone" in (channel.can_mention(role, by=asker) or "")
    events.mentionable = True
    assert channel.can_mention(role, by=asker) is None
    events.mentionable = False
    raw.refused.add("mention_everyone")
    assert "I need Mention Everyone" in (channel.can_mention(role) or "")
    everyone = view.get_role(guild.id)
    if everyone is not None:
        assert "@everyone" in (channel.can_mention(everyone) or "")


async def test_the_harness_votes_and_ends_a_poll_before_discord_finalises_it(
    teabot_harness: HarnessFactory,
) -> None:
    harness = await teabot_harness(plugin)
    raw = harness.guild.add_channel(FakeChannel(name="votes"))
    channel = guild_view(harness.guild).get_text_channel(raw.id)
    assert channel is not None
    message = await channel.send(poll=poll())
    [sent] = raw.sent
    assert isinstance(sent, FakeMessage)
    harness.vote(sent, harness.member, "Tea Maze")
    harness.vote(sent, harness.member, "Battleships")
    harness.end_poll(sent, final=False)
    read = await channel.read_poll(message.id)
    assert read is not None and not read.final
    assert [a.votes for a in read.answers] == [0, 1]
    with pytest.raises(ValueError, match="ended"):
        harness.vote(sent, harness.member, "Tea Maze")
    harness.end_poll(sent)
    final = await channel.read_poll(message.id)
    assert final is not None and final.final
