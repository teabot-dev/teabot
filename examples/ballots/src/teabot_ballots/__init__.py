"""teabot-ballots, the plugin the tutorial builds. Imports only `teabot.sdk`.

- `ballots.start` posts a ballot with two to four numbered options, and schedules the job that closes it.
- `ballots.vote` records a member's one vote in a ballot; voting again changes it.
- `ballots.list` shows the running ballots, with how many have voted.
- `ballots.delete` deletes a ballot and its votes, after a plan.
- The `close` job posts the results when time is up.
"""

# --8<-- [start:imports]
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, Field

from teabot.sdk import (
    DESTRUCTIVE,
    READ,
    ActionError,
    Context,
    Diff,
    DiffEntry,
    Plugin,
    RoleRef,
    TextChannel,
    UntrustedText,
)

# --8<-- [end:imports]


# --8<-- [start:plugin]
class Cfg(BaseModel):
    default_minutes: int = Field(default=60, ge=1, le=10080)
    voter_role: RoleRef | None = None


plugin = Plugin("ballots", config=Cfg, description="Ballots that members vote in with buttons.")
type Ctx = Context[Cfg]
# --8<-- [end:plugin]

# --8<-- [start:queries]
START = """
INSERT INTO ballots (guild_id, channel_id, question, options, ends_at) VALUES ($1, $2, $3, $4, $5) RETURNING id
"""
VOTE = """
INSERT INTO votes (guild_id, ballot_id, member_id, choice)
SELECT guild_id, id, $3, $4 FROM ballots
WHERE guild_id = $1 AND id = $2 AND state = 'running' AND ends_at > now() AND $4 <= cardinality(options)
ON CONFLICT (guild_id, ballot_id, member_id) DO UPDATE SET choice = EXCLUDED.choice
"""
RUNNING = """
SELECT p.id, p.question, p.ends_at, count(v.member_id) AS voters
FROM ballots p LEFT JOIN votes v ON v.guild_id = p.guild_id AND v.ballot_id = p.id
WHERE p.guild_id = $1 AND p.state = 'running'
GROUP BY p.id ORDER BY p.ends_at
"""
LOCK_RUNNING = """
SELECT channel_id, question, options FROM ballots WHERE guild_id = $1 AND id = $2 AND state = 'running' FOR UPDATE
"""
COUNTS = "SELECT choice, count(*) AS votes FROM votes WHERE guild_id = $1 AND ballot_id = $2 GROUP BY choice"
CLOSE = "UPDATE ballots SET state = 'closed' WHERE guild_id = $1 AND id = $2"
ONE = "SELECT id, question FROM ballots WHERE guild_id = $1 AND id = $2"
DELETE = "DELETE FROM ballots WHERE guild_id = $1 AND id = $2"
# --8<-- [end:queries]


# --8<-- [start:start]
class Started(BaseModel):
    ballot: int
    ends_at: datetime

    def render(self) -> str:
        return f"Ballot {self.ballot} started; it closes <t:{int(self.ends_at.timestamp())}:R>."


type Choice = Annotated[str, Field(min_length=1, max_length=100)]


@plugin.action(needs="manage_messages")
async def start(
    ctx: Ctx,
    channel: TextChannel,
    question: Annotated[str, Field(max_length=200)],
    option_1: Choice,
    option_2: Choice,
    option_3: Choice | None = None,
    option_4: Choice | None = None,
    minutes: Annotated[int, Field(ge=1, le=10080)] | None = None,
) -> Started:
    """Start a ballot.

    Args:
        channel: Where to post the ballot.
        question: What the ballot asks.
        option_1: The first option.
        option_2: The second option.
        option_3: A third option.
        option_4: A fourth option.
        minutes: How long it runs; the server's default if left out.
    """
    options = [o for o in (option_1, option_2, option_3, option_4) if o]
    ends_at = ctx.now + timedelta(minutes=minutes or ctx.config.default_minutes)
    async with ctx.db.transaction() as tx:
        ballot = await tx.fetchval(int, START, ctx.guild.id, channel.id, question, options, ends_at)
        await tx.schedule(close.bind(ballot=ballot), at=ends_at)
    numbered = "\n".join(f"{n}. {option}" for n, option in enumerate(options, start=1))
    await channel.send(
        f"**Ballot {ballot}: {question}**\n{numbered}\nVote with `/ballots vote ballot:{ballot}`."
    )
    return Started(ballot=ballot, ends_at=ends_at)


# --8<-- [end:start]


# --8<-- [start:vote]
@plugin.action()
async def vote(ctx: Ctx, ballot: int, option: Annotated[int, Field(ge=1, le=4)]) -> str:
    """Vote in a running ballot, or change your vote.

    Args:
        ballot: The ballot's number, from /ballots list.
        option: The number of the option you choose.
    """
    role = ctx.config.voter_role
    if role is not None:
        member = await ctx.guild.get_member(ctx.actor.user_id)
        if member is None or role not in member.role_ids:
            raise ActionError("You need the voter role to vote here.")
    done = await ctx.db.execute(VOTE, ctx.guild.id, ballot, ctx.actor.user_id, option)
    if done == "INSERT 0 0":
        raise ActionError("That ballot is not running, or has no option with that number.")
    return f"Your vote is option {option}."


# --8<-- [end:vote]


# --8<-- [start:list]
class Running(BaseModel):
    id: int
    question: UntrustedText
    ends_at: datetime
    voters: int


@plugin.action(risk=READ, reply="public")
async def list_(ctx: Ctx) -> list[Running]:
    """The running ballots."""
    return await ctx.db.fetch(Running, RUNNING, ctx.guild.id)


# --8<-- [end:list]


# --8<-- [start:close]
class Open(BaseModel):
    channel_id: int
    question: str
    options: list[str]


class Count(BaseModel):
    choice: int
    votes: int


@plugin.job
async def close(ctx: Ctx, ballot: int) -> None:
    async with ctx.db.transaction() as tx:
        found = await tx.fetchrow(Open, LOCK_RUNNING, ctx.guild.id, ballot)
        if found is None:
            return
        counts = {c.choice: c.votes for c in await tx.fetch(Count, COUNTS, ctx.guild.id, ballot)}
        await tx.execute(CLOSE, ctx.guild.id, ballot)
        lines = [f"{n}. {option}: {counts.get(n, 0)}" for n, option in enumerate(found.options, start=1)]
        await tx.send(
            found.channel_id, f"**Ballot {ballot} has closed: {found.question}**\n" + "\n".join(lines)
        )


# --8<-- [end:close]


# --8<-- [start:delete]
class Found(BaseModel):
    id: int
    question: UntrustedText


@plugin.action(risk=DESTRUCTIVE, needs="manage_messages")
async def delete(ctx: Ctx, ballot: int) -> str:
    """Delete a ballot and its votes.

    Args:
        ballot: The ballot's number, from /ballots list.
    """
    async with ctx.db.transaction() as tx:
        await tx.execute(DELETE, ctx.guild.id, ballot)
        await tx.unschedule(close.bind(ballot=ballot))
    return f"Deleted ballot {ballot}."


@delete.plan
async def plan_delete(ctx: Ctx, ballot: int) -> Diff:
    found = await ctx.db.fetchrow(Found, ONE, ctx.guild.id, ballot)
    if found is None:
        raise ActionError("No ballot has that number.")
    return Diff(entries=[DiffEntry(change="delete", target=found.question, key=found.id)])


# --8<-- [end:delete]
