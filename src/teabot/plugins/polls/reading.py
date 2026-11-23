"""Reading a poll's final results once it ends (polls.md), with the `read_results` job.

The job decides on Discord's `final` alone. Until then it reads again a minute later, ten times, then keeps what it
read, marked not final. A deleted message makes the poll `gone`. A channel the bot can no longer see is tried again
hourly for a day, in case the permission is fixed, then the poll is `unread`. Every outcome but `unread` emits
`polls.ended`.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

from teabot.sdk import MessageHidden, SentPoll

from . import RECORD, AnswerVotes, Ctx, Ended, Record, message_url, plugin

NOT_FINAL_TRIES = 10
HIDDEN_TRIES = 24
AGAIN = timedelta(minutes=1)
HIDDEN_AGAIN = timedelta(hours=1)


def key(poll: int) -> str:
    return f"poll:{poll}"


async def schedule_read(tx: Any, poll: int, at: datetime, tries: int = 0) -> None:
    """Read the poll's results at `at`, moving any pending read: one key per poll, whatever `tries` is."""
    await tx.schedule(read_results.bind(poll=poll, tries=tries), at=at, key=key(poll))


@plugin.job
async def read_results(ctx: Ctx, poll: int, tries: int) -> None:
    found = await ctx.db.fetchrow(Record, RECORD, ctx.guild.id, poll)
    if found is None or found.state != "running":
        return
    channel = ctx.guild.get_text_channel(found.channel_id)
    try:
        read = await channel.read_poll(found.message_id) if channel is not None else None
    except MessageHidden:
        await _hidden(ctx, found, tries)
        return
    if read is None:
        await _settle(ctx, found, "gone", None)
        return
    if read.ends_at is not None and read.ends_at > ctx.now:
        # Discord hasn't ended it yet, as after a clock difference: read it once it has.
        async with ctx.db.transaction() as tx:
            await schedule_read(tx, poll, read.ends_at + AGAIN, tries)
        return
    if not read.final and tries + 1 < NOT_FINAL_TRIES:
        async with ctx.db.transaction() as tx:
            await schedule_read(tx, poll, ctx.now + AGAIN, tries + 1)
        return
    # A poll never finalised whose every count is zero may be one Discord sent no counts for: keep none.
    await _settle(ctx, found, "counted" if read.final or read.votes > 0 else "uncounted", read)


async def _hidden(ctx: Ctx, found: Record, tries: int) -> None:
    async with ctx.db.transaction() as tx:
        if tries + 1 < HIDDEN_TRIES:
            await schedule_read(tx, found.id, ctx.now + HIDDEN_AGAIN, tries + 1)
        else:
            await tx.execute(
                "UPDATE polls SET state = 'unread' WHERE guild_id = $1 AND id = $2", ctx.guild.id, found.id
            )


async def _settle(
    ctx: Ctx, found: Record, outcome: Literal["counted", "uncounted", "gone"], read: SentPoll | None
) -> None:
    """Keep the poll's results, if any, and emit `polls.ended`."""
    counted = outcome == "counted" and read is not None
    votes = [a.votes for a in read.answers] if counted and read is not None else None
    final = bool(read.final) if counted and read is not None else False
    async with ctx.db.transaction() as tx:
        done = await tx.execute(
            "UPDATE polls SET state = $3, votes = $4, final = $5 WHERE guild_id = $1 AND id = $2 AND state = 'running'",
            ctx.guild.id,
            found.id,
            "gone" if outcome == "gone" else "ended",
            votes,
            final,
        )
        if done == "UPDATE 0":
            return
        texts = [a.text for a in read.answers] if counted and read is not None else found.answers
        await tx.emit(
            Ended(
                poll=found.id,
                channel=found.channel_id,
                message_url=message_url(ctx, found),
                started_by=found.started_by or None,
                question=found.question,
                answers=[
                    AnswerVotes(text=t, votes=votes[n] if votes is not None else None)
                    for n, t in enumerate(texts)
                ],
                votes=sum(votes) if votes is not None else None,
                multiple=found.multiple,
                final=final,
                outcome=outcome,
            )
        )
