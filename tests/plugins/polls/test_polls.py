"""teabot-polls: Discord's own polls, started, ended early, listed, and kept with their results once read."""

import json
from typing import Any

import discord
import pytest

from teabot.plugins.polls import end, list_, plugin, results, start
from teabot.testing import FakeChannel, FakeMessage, HarnessFactory, PluginHarness

NONE = discord.Permissions.none()


class Room:
    """A server with a channel for polls, and a poll started there."""

    def __init__(self, harness: PluginHarness, channel: FakeChannel) -> None:
        self.harness, self.channel = harness, channel

    @property
    def message(self) -> FakeMessage:
        return self.channel.sent[-1]

    async def start(self, **inputs: Any) -> int:
        out = await self.harness.call(
            start,
            channel=self.channel,
            question="Which event next?",
            answers=["Tea Maze", "Battleships"],
            **inputs,
        )
        return out.poll

    async def events(self) -> list[dict[str, Any]]:
        db: Any = self.harness.admin
        rows = await db.fetch("SELECT payload FROM core.events WHERE name = 'ended' ORDER BY id")
        return [json.loads(r["payload"]) for r in rows]

    async def state(self, poll: int) -> str:
        db: Any = self.harness.admin
        schema = self.harness.pools.naming.plugin_schema(self.harness.plugin_id)
        return await db.fetchval(f'SELECT state FROM "{schema}".polls WHERE id = $1', poll)

    async def settle(self, *, minutes: int = 1) -> None:
        """Let time pass and the job read the poll."""
        await self.harness.advance(minutes=minutes)
        await self.harness.run_jobs()


async def room(teabot_harness: HarnessFactory) -> Room:
    harness = await teabot_harness(plugin)
    return Room(harness, harness.guild.add_channel(FakeChannel(name="votes")))


async def test_a_poll_runs_and_its_final_results_are_kept_and_announced(
    teabot_harness: HarnessFactory,
) -> None:
    r = await room(teabot_harness)
    poll = await r.start(duration="1 hour")
    sam = r.harness.guild.add_member(NONE, name="sam")
    r.harness.vote(r.message, sam, "Tea Maze")
    r.harness.vote(r.message, r.harness.member, "Tea Maze")
    [listed] = await r.harness.call(list_)
    assert (listed.id, listed.question) == (poll, "Which event next?")
    live = await r.harness.call(results, poll=poll)
    assert ([a.votes for a in live.answers], live.status) == ([2, 0], "running")
    await r.settle(minutes=61)
    assert await r.harness.call(list_) == []
    assert await r.state(poll) == "ended"
    kept = await r.harness.call(results, poll=poll)
    assert ([a.votes for a in kept.answers], kept.votes, kept.status) == ([2, 0], 2, "final")
    [event] = await r.events()
    assert (event["outcome"], event["final"], event["votes"], event["started_by"]) == (
        "counted",
        True,
        2,
        str(r.harness.member.id),
    )
    assert event["answers"][0] == {"text": "Tea Maze", "votes": 2}


async def test_ending_early_shows_the_votes_so_far_then_reads_the_results(
    teabot_harness: HarnessFactory,
) -> None:
    r = await room(teabot_harness)
    poll = await r.start()
    r.harness.vote(r.message, r.harness.member, "Battleships")
    plan = await r.harness.plan(end, poll=poll)
    assert plan.notes == ["Votes so far: Tea Maze 0, Battleships 1"]
    await r.harness.confirm(plan)
    assert r.message.poll is not None and r.message.poll.ended
    assert await r.harness.call(list_) == []
    await r.settle()
    [event] = await r.events()
    assert (event["outcome"], [a["votes"] for a in event["answers"]]) == ("counted", [0, 1])
    with pytest.raises(Exception, match="isn't running"):
        await r.harness.plan(end, poll=poll)


async def test_counts_never_finalised_are_kept_as_not_final_or_as_none(
    teabot_harness: HarnessFactory,
) -> None:
    r = await room(teabot_harness)
    voted = await r.start(duration="1 hour")
    r.harness.vote(r.message, r.harness.member, "Tea Maze")
    assert r.message.poll is not None
    r.message.poll.finalised = False
    empty = await r.start(duration="1 hour")
    assert r.message.poll is not None
    r.message.poll.finalised = False
    await r.settle(minutes=61)
    for _ in range(9):
        await r.settle()
    kept = await r.harness.call(results, poll=voted)
    assert ([a.votes for a in kept.answers], kept.status) == (
        [1, 0],
        "ended; Discord hadn't finalised these counts",
    )
    none = await r.harness.call(results, poll=empty)
    assert (none.votes, none.status) == (None, "ended, but Discord sent no counts")
    assert [(e["outcome"], e["final"]) for e in await r.events()] == [
        ("counted", False),
        ("uncounted", False),
    ]


async def test_a_deleted_message_makes_the_poll_gone_and_says_so(teabot_harness: HarnessFactory) -> None:
    r = await room(teabot_harness)
    poll = await r.start(duration="1 hour")
    await r.message.delete()
    live = await r.harness.call(results, poll=poll)
    assert live.status == "Its message is gone."
    await r.settle(minutes=61)
    assert await r.state(poll) == "gone"
    [event] = await r.events()
    assert (event["outcome"], event["votes"]) == ("gone", None)


async def test_a_channel_the_bot_lost_is_tried_for_a_day_then_unread(teabot_harness: HarnessFactory) -> None:
    r = await room(teabot_harness)
    poll = await r.start(duration="1 hour")
    r.channel.hidden_from.add(r.harness.guild.me.id)
    await r.settle(minutes=61)
    for _ in range(22):
        await r.settle(minutes=60)
    assert await r.state(poll) == "running"
    await r.settle(minutes=60)
    assert await r.state(poll) == "unread"
    kept = await r.harness.call(results, poll=poll)
    assert "lost sight of its channel" in kept.status
    assert await r.events() == []


async def test_a_permission_fixed_within_the_day_still_gets_the_results(
    teabot_harness: HarnessFactory,
) -> None:
    r = await room(teabot_harness)
    poll = await r.start(duration="1 hour")
    r.channel.hidden_from.add(r.harness.guild.me.id)
    await r.settle(minutes=61)
    r.channel.hidden_from.clear()
    await r.settle(minutes=60)
    assert await r.state(poll) == "ended"


async def test_a_role_ping_is_refused_when_it_would_not_reach_anyone(teabot_harness: HarnessFactory) -> None:
    r = await room(teabot_harness)
    events_role = r.harness.guild.add_role("events")
    mod = r.harness.guild.add_member(discord.Permissions(manage_messages=True), name="mod")
    with pytest.raises(Exception, match="you need Mention Everyone"):
        await r.harness.as_member(mod).call(
            start, channel=r.channel, question="Q?", answers=["a", "b"], ping=events_role
        )
    await r.start(ping=events_role)
    assert (r.message.content or "").startswith(f"<@&{events_role.id}>")


async def test_a_poll_discord_would_refuse_is_refused_before_posting(teabot_harness: HarnessFactory) -> None:
    r = await room(teabot_harness)
    with pytest.raises(Exception, match="Each answer appears once"):
        await r.harness.call(start, channel=r.channel, question="Q?", answers=["Yes", "yes"])
    r.channel.refused.add("send_polls")
    with pytest.raises(Exception, match="I need Send Polls"):
        await r.start()
    assert r.channel.sent == []


async def test_a_poll_that_could_not_be_recorded_is_taken_back(
    teabot_harness: HarnessFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await room(teabot_harness)

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the queue is down")

    monkeypatch.setattr("teabot.plugins.polls.reading.schedule_read", broken)
    with pytest.raises(Exception, match="the queue is down"):
        await r.start()
    assert r.message.deleted


async def test_every_action_needs_manage_messages(teabot_harness: HarnessFactory) -> None:
    r = await room(teabot_harness)
    poll = await r.start()
    sam = r.harness.as_member(r.harness.guild.add_member(NONE, name="sam"))
    with pytest.raises(Exception, match="permission"):
        await sam.call(list_)
    with pytest.raises(Exception, match="permission"):
        await sam.call(results, poll=poll)
    with pytest.raises(Exception, match="permission"):
        await sam.plan(end, poll=poll)


async def test_the_poll_option_suggests_running_polls_to_end_and_any_for_results(
    teabot_harness: HarnessFactory,
) -> None:
    r = await room(teabot_harness)
    done = await r.start(duration="1 hour")
    await r.settle(minutes=61)
    running = await r.start()
    assert [c.value for c in await r.harness.autocomplete(end, "poll", "")] == [running]
    assert [c.value for c in await r.harness.autocomplete(results, "poll", "")] == [running, done]
