"""teabot-polls: Discord's own polls, started, ended, listed and kept (polls.md). Imports only `teabot.sdk`.

- `polls.start` posts a poll and records it, and `polls.end` ends one early, after a plan.
- `polls.list` shows the running polls, from teabot's record alone, and `polls.results` one poll's counts.
- The `read_results` job, in `reading`, reads a poll's final counts once it ends, keeps them, and emits
  `polls.ended`.

Members vote in Discord, which counts and shows the results and posts its own results message. Every action needs
Manage Messages: a poll may sit in a channel some members can't see.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from teabot.sdk import (
    DESTRUCTIVE,
    READ,
    ActionError,
    Choice,
    Context,
    Diff,
    DiffEntry,
    EmptyConfig,
    Event,
    MemberRef,
    MessageHidden,
    Plugin,
    Poll,
    Role,
    SentPoll,
    TextChannel,
    UntrustedText,
)


class AnswerVotes(BaseModel):
    text: UntrustedText
    votes: int | None


class Ended(BaseModel):
    poll: int
    channel: int
    message_url: str
    started_by: MemberRef | None
    question: UntrustedText
    answers: list[AnswerVotes]
    votes: int | None
    multiple: bool
    final: bool
    outcome: Literal["counted", "uncounted", "gone"]


plugin = Plugin(
    "polls",
    description="Discord's own polls: start them, end them early, list them, and keep their results.",
    emits=[
        Event(
            "ended",
            Ended,
            "A poll ended and its results were read; outcome says whether Discord counted it or its message was gone",
        )
    ],
)
type Ctx = Context[EmptyConfig]

type Duration = Literal["1 hour", "4 hours", "8 hours", "1 day", "3 days", "1 week", "2 weeks"]
DURATIONS: dict[str, timedelta] = {
    "1 hour": timedelta(hours=1),
    "4 hours": timedelta(hours=4),
    "8 hours": timedelta(hours=8),
    "1 day": timedelta(days=1),
    "3 days": timedelta(days=3),
    "1 week": timedelta(weeks=1),
    "2 weeks": timedelta(weeks=2),
}
SETTLE = timedelta(minutes=1)
"""How long after a poll ends its results are read, which gives Discord time to finalise them."""

type Question = Annotated[str, Field(min_length=1, max_length=300)]
type Answers = Annotated[list[str], Field(min_length=2, max_length=10)]

RECORD = """
SELECT id, channel_id, message_id, question, answers, multiple, ends_at, ended_at, state, votes, final, started_by
FROM polls WHERE guild_id = $1 AND id = $2
"""
RUNNING = "state = 'running' AND coalesce(ended_at, ends_at) > now()"


class Record(BaseModel):
    id: int
    channel_id: int
    message_id: int
    question: str
    answers: list[str]
    multiple: bool
    ends_at: datetime
    ended_at: datetime | None
    state: str
    votes: list[int] | None
    final: bool | None
    started_by: int | None

    def running(self, now: datetime) -> bool:
        """Whether it still takes votes, whatever its job has yet to read."""
        return self.state == "running" and (self.ended_at or self.ends_at) > now


async def record_of(ctx: Ctx, poll: int) -> Record:
    found = await ctx.db.fetchrow(Record, RECORD, ctx.guild.id, poll)
    if found is None:
        raise ActionError("No poll has that number.")
    return found


def message_url(ctx: Ctx, found: Record) -> str:
    return f"https://discord.com/channels/{ctx.guild.id}/{found.channel_id}/{found.message_id}"


class Started(BaseModel):
    poll: int
    url: str
    ends_at: datetime

    def render(self) -> str:
        return f"Poll {self.poll} started: {self.url}. It ends <t:{int(self.ends_at.timestamp())}:R>."


@plugin.action(needs="manage_messages")
async def start(
    ctx: Ctx,
    channel: TextChannel,
    question: Question,
    answers: Answers,
    duration: Duration = "1 day",
    multiple: bool = False,
    ping: Role | None = None,
) -> Started:
    """Start a poll, which members vote in with a click.

    Args:
        channel: Where to post the poll
        question: What the poll asks
        answers: An answer members can choose
        duration: How long it runs
        multiple: Whether members may choose more than one answer
        ping: A role to ping with the poll
    """
    try:
        poll = Poll(question, tuple(answers), DURATIONS[duration], multiple)
    except ValueError as exc:
        raise ActionError(str(exc)) from None
    if ping is not None:
        member = await ctx.guild.get_member(ctx.actor.user_id)
        problem = channel.can_mention(ping, by=member)
        if problem is not None:
            raise ActionError(problem)
    message = await channel.send(poll=poll, ping_roles=[ping.id] if ping is not None else ())
    ends_at = message.poll_ends_at or ctx.now + poll.duration
    try:
        async with ctx.db.transaction() as tx:
            poll_id = await tx.fetchval(
                int,
                "INSERT INTO polls (guild_id, channel_id, message_id, question, answers, multiple, ends_at, "
                "started_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
                ctx.guild.id,
                channel.id,
                message.id,
                poll.question,
                list(poll.answers),
                poll.multiple,
                ends_at,
                ctx.actor.user_id,
            )
            await reading.schedule_read(tx, poll_id, ends_at + SETTLE)
    except Exception:
        # No poll runs without a record: take back the one posted.
        await message.delete()
        raise
    return Started(poll=poll_id, url=message.jump_url, ends_at=ends_at)


@plugin.action(risk=DESTRUCTIVE, needs="manage_messages")
async def end(ctx: Ctx, poll: int) -> str:
    """End a running poll early, which stops the voting. It can't be undone.

    Args:
        poll: The poll, from /polls list
    """
    found = await record_of(ctx, poll)
    if not found.running(ctx.now):
        raise ActionError("That poll isn't running.")
    channel = ctx.guild.get_text_channel(found.channel_id)
    if channel is None:
        raise ActionError("That poll's channel is gone.")
    await channel.end_poll(found.message_id)
    async with ctx.db.transaction() as tx:
        await tx.execute(
            f"UPDATE polls SET ended_at = now() WHERE guild_id = $1 AND id = $2 AND {RUNNING}",
            ctx.guild.id,
            poll,
        )
        await reading.schedule_read(tx, poll, ctx.now + SETTLE)
    return f"Ended poll {poll}. Its results are kept in a minute or so, once Discord finalises them."


@end.plan
async def plan_end(ctx: Ctx, poll: int) -> Diff:
    found = await record_of(ctx, poll)
    if not found.running(ctx.now):
        raise ActionError("That poll isn't running.")
    live = await live_poll(ctx, found)
    notes: list[str] = []
    if isinstance(live, SentPoll):
        notes.append("Votes so far: " + ", ".join(f"{a.text} {a.votes}" for a in live.answers))
    else:
        notes.append(live)
    return Diff(entries=[DiffEntry(change="end", target=found.question, key=poll)], notes=notes)


async def live_poll(ctx: Ctx, found: Record) -> SentPoll | str:
    """The poll as Discord shows it now, or why it can't be read."""
    channel = ctx.guild.get_text_channel(found.channel_id)
    if channel is None:
        return "Its channel is gone."
    try:
        read = await channel.read_poll(found.message_id)
    except MessageHidden as exc:
        return str(exc)
    return read if read is not None else "Its message is gone."


class Listed(BaseModel):
    id: int
    question: UntrustedText
    channel_id: int
    ends_at: datetime

    def render(self) -> str:
        return (
            f"{self.id}: {self.question} in <#{self.channel_id}>, ends <t:{int(self.ends_at.timestamp())}:R>"
        )


@plugin.action(risk=READ, needs="manage_messages")
async def list_(ctx: Ctx) -> list[Listed]:
    """The server's running polls."""
    return await ctx.db.fetch(
        Listed,
        f"SELECT id, question, channel_id, coalesce(ended_at, ends_at) AS ends_at FROM polls "
        f"WHERE guild_id = $1 AND {RUNNING} ORDER BY ends_at, id",
        ctx.guild.id,
    )


class Results(BaseModel):
    poll: int
    question: UntrustedText
    url: str
    answers: list[AnswerVotes]
    votes: int | None
    multiple: bool
    status: str

    def render(self) -> str:
        lines = [f"**Poll {self.poll}: {self.question}** ({self.status})", self.url]
        lines += [f"- {a.text}: {a.votes if a.votes is not None else '?'}" for a in self.answers]
        if self.votes is not None:
            lines.append(
                f"{self.votes} votes in all" + (", some members choosing several" if self.multiple else "")
            )
        return "\n".join(lines)


@plugin.action(risk=READ, needs="manage_messages")
async def results(ctx: Ctx, poll: int) -> Results:
    """A poll's results: the counts so far while it runs, and the kept final counts once it has ended.

    Args:
        poll: The poll, from /polls list
    """
    found = await record_of(ctx, poll)
    if found.state != "running":
        return shown(ctx, found, *kept(found))
    live = await live_poll(ctx, found)
    if isinstance(live, str):
        return shown(ctx, found, [AnswerVotes(text=a, votes=None) for a in found.answers], None, live)
    answers = [AnswerVotes(text=a.text, votes=a.votes) for a in live.answers]
    status = "final" if live.final else ("ended, being counted" if not found.running(ctx.now) else "running")
    return shown(ctx, found, answers, live.votes, status)


def shown(ctx: Ctx, found: Record, answers: list[AnswerVotes], votes: int | None, status: str) -> Results:
    return Results(
        poll=found.id,
        question=found.question,
        url=message_url(ctx, found),
        answers=answers,
        votes=votes,
        multiple=found.multiple,
        status=status,
    )


def kept(found: Record) -> tuple[list[AnswerVotes], int | None, str]:
    """A poll's kept results, once its job has read them, or why there are none."""
    if found.state == "gone":
        status = "its message was deleted before its results were read"
    elif found.state == "unread":
        status = "the bot lost sight of its channel, so its results weren't read"
    elif found.votes is None:
        status = "ended, but Discord sent no counts"
    else:
        status = "final" if found.final else "ended; Discord hadn't finalised these counts"
    votes: list[int | None] = list(found.votes) if found.votes is not None else [None] * len(found.answers)
    answers = [AnswerVotes(text=a, votes=v) for a, v in zip(found.answers, votes, strict=True)]
    return answers, sum(found.votes) if found.votes is not None else None, status


async def poll_choices(ctx: Ctx, typed: str, *, running: bool) -> list[Choice[int]]:
    where = f"AND {RUNNING}" if running else ""
    rows = await ctx.db.fetch(
        f"SELECT id, question FROM polls WHERE guild_id = $1 {where} ORDER BY id DESC LIMIT 200", ctx.guild.id
    )
    return [
        Choice(f"{r['id']}: {r['question']}"[:100], int(r["id"]))
        for r in rows
        if typed.lower() in f"{r['id']} {r['question']}".lower()
    ][:25]


@end.autocomplete("poll")
async def running_choices(ctx: Ctx, typed: str) -> list[Choice[int]]:
    return await poll_choices(ctx, typed, running=True)


@results.autocomplete("poll")
async def any_choices(ctx: Ctx, typed: str) -> list[Choice[int]]:
    return await poll_choices(ctx, typed, running=False)


# The job registers on the plugin.
from . import reading as reading  # noqa: E402
