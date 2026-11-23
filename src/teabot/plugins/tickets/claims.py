"""Claims: members claim open tickets, and handlers accept, reject or revoke them.

Every write locks the tickets it touches first, in ascending ID order, so concurrent calls never deadlock, a claim
can't land on a ticket an accept just closed, and for one ticket a later event has a higher ID. Counts across a queue,
the daily limit, are taken under a lock on the queue's row.
"""

from datetime import timedelta
from typing import Annotated, Any

from pydantic import BaseModel, Field

from teabot.sdk import ActionError, Button, Choice, MemberRef, UntrustedText

from . import (
    WAITING_MAX,
    Accepted,
    Ctx,
    Rejected,
    Revoked,
    handled,
    handles,
    may_claim,
    member_of,
    plugin,
    queue_of,
    refresh_board,
)

type Note = Annotated[str, Field(max_length=200)]
type Reason = Annotated[str, Field(max_length=200)]
type ClaimIds = Annotated[list[int], Field(min_length=1, max_length=10)]

CLAIM = """
SELECT c.id, c.ticket_id, c.member, c.note, c.state, c.handler_post, t.title, t.body, t.key,
       t.state AS ticket_state, t.queue_id, q.name AS queue_name
FROM claims c JOIN tickets t ON t.guild_id = c.guild_id AND t.id = c.ticket_id
JOIN queues q ON q.guild_id = t.guild_id AND q.id = t.queue_id
WHERE c.guild_id = $1 AND c.id = ANY($2::bigint[]) ORDER BY c.ticket_id, c.id
"""
WAITING_ON = """
SELECT c.id, c.ticket_id, c.member, c.note, c.state, c.handler_post, t.title, t.body, t.key,
       t.state AS ticket_state, t.queue_id, q.name AS queue_name
FROM claims c JOIN tickets t ON t.guild_id = c.guild_id AND t.id = c.ticket_id
JOIN queues q ON q.guild_id = t.guild_id AND q.id = t.queue_id
WHERE c.guild_id = $1 AND c.ticket_id = $2 AND c.state = 'waiting' ORDER BY c.id
"""
LOCK_TICKETS = "SELECT id FROM tickets WHERE guild_id = $1 AND id = ANY($2::bigint[]) ORDER BY id FOR UPDATE"
SETTLE = """
UPDATE claims SET state = $3, settled_by = $4, settled_at = now(), reason = $5 WHERE guild_id = $1 AND id = $2
"""
TOUCH = "UPDATE tickets SET updated_at = now() WHERE guild_id = $1 AND id = $2"


class Claim(BaseModel):
    id: int
    ticket_id: int
    member: int
    note: str
    state: str
    handler_post: int | None
    title: str
    body: str
    key: str | None
    ticket_state: str
    queue_id: int
    queue_name: str


def _base(c: Claim) -> dict[str, Any]:
    return {
        "queue": c.queue_id,
        "queue_name": c.queue_name,
        "ticket": c.ticket_id,
        "key": c.key,
        "title": c.title,
        "claim": c.id,
        "note": c.note,
        "claimant": c.member,
    }


def _handler_text(c: Claim) -> str:
    lines = [f"**{c.queue_name}**: <@{c.member}> claims **{c.title}** (ticket {c.ticket_id}, claim {c.id})"]
    if c.note:
        lines.append(f"Note: {c.note}")
    if c.body:
        lines.append(c.body)
    return "\n".join(lines)


async def _settled_post(tx: Any, c: Claim, outcome: str) -> None:
    """Edit the claim's post in the handlers' channel to say how it was settled, with its buttons gone."""
    if c.handler_post is not None:
        await tx.edit(c.handler_post, f"{_handler_text(c)}\n{outcome}")


async def _locked(ctx: Ctx, tx: Any, ids: list[int]) -> list[Claim]:
    """The claims, after locking their tickets in ascending order; `ActionError` for one that isn't here."""
    first = await tx.fetch(Claim, CLAIM, ctx.guild.id, ids)
    missing = sorted(set(ids) - {c.id for c in first})
    if missing:
        raise ActionError(f"There is no claim {missing[0]} here.")
    await tx.execute(LOCK_TICKETS, ctx.guild.id, sorted({c.ticket_id for c in first}))
    return await tx.fetch(Claim, CLAIM, ctx.guild.id, ids)


async def _check_handles(ctx: Ctx, tx: Any, claims: list[Claim]) -> None:
    member = await member_of(ctx)
    for queue_id in sorted({c.queue_id for c in claims}):
        queue = await queue_of(ctx, queue_id, within=tx)
        if not handles(ctx, member, queue):
            raise ActionError(f"Only {queue.name}'s handlers may settle its claims.")


async def reject_waiting(ctx: Ctx, tx: Any, ticket: int, keep: int | None, reason: str) -> int:
    """Reject the ticket's waiting claims but `keep`, each with `tickets.rejected`. The ticket is locked already."""
    rejected = 0
    for c in await tx.fetch(Claim, WAITING_ON, ctx.guild.id, ticket):
        if c.id == keep:
            continue
        await tx.execute(SETTLE, ctx.guild.id, c.id, "rejected", ctx.actor.user_id, reason)
        await tx.emit(Rejected(**_base(c), reason=reason))
        await _settled_post(tx, c, f"Rejected: {reason}")
        rejected += 1
    return rejected


@plugin.action()
async def accept(ctx: Ctx, claims: ClaimIds) -> str:
    """Accept claims, up to ten. Each closes its ticket, and rejects the ticket's other waiting claims.

    Args:
        claims: The claims' numbers, as /tickets waiting shows them
    """
    async with ctx.db.transaction() as tx:
        found = await _locked(ctx, tx, claims)
        if len({c.ticket_id for c in found}) != len(found):
            raise ActionError("Accept one claim per ticket in a call.")
        await _check_handles(ctx, tx, found)
        for c in found:
            if c.state != "waiting" or c.ticket_state != "open":
                raise ActionError(f"Claim {c.id} isn't waiting on an open ticket.")
        for c in found:
            await tx.execute(SETTLE, ctx.guild.id, c.id, "accepted", ctx.actor.user_id, None)
            await tx.execute(
                "UPDATE tickets SET state = 'closed', resolution = 'accepted', closed_at = now(), updated_at = now() "
                "WHERE guild_id = $1 AND id = $2",
                ctx.guild.id,
                c.ticket_id,
            )
            await tx.emit(Accepted(**_base(c)))
            await _settled_post(tx, c, f"Accepted by <@{ctx.actor.user_id}>.")
            await reject_waiting(ctx, tx, c.ticket_id, c.id, "another claim was accepted")
        for queue_id in sorted({c.queue_id for c in found}):
            await refresh_board(ctx, tx, queue_id)
    return f"Accepted {', '.join(f'{c.title} for <@{c.member}>' for c in found)}."


@plugin.action()
async def reject(ctx: Ctx, claims: ClaimIds, reason: Reason = "") -> str:
    """Reject claims, up to ten. Their tickets stay open for others.

    Args:
        claims: The claims' numbers
        reason: Why, which the claimant and subscribers see
    """
    said = reason or "rejected by a handler"
    async with ctx.db.transaction() as tx:
        found = await _locked(ctx, tx, claims)
        await _check_handles(ctx, tx, found)
        for c in found:
            if c.state != "waiting":
                raise ActionError(f"Claim {c.id} isn't waiting.")
        for c in found:
            await tx.execute(SETTLE, ctx.guild.id, c.id, "rejected", ctx.actor.user_id, said)
            await tx.execute(TOUCH, ctx.guild.id, c.ticket_id)
            await tx.emit(Rejected(**_base(c), reason=said))
            await _settled_post(tx, c, f"Rejected by <@{ctx.actor.user_id}>: {said}")
    return f"Rejected {len(found)} claim{'s' if len(found) != 1 else ''}."


@plugin.action()
async def revoke(ctx: Ctx, ticket: int, reason: Reason = "") -> str:
    """Undo an accept: the accepted claim is revoked and the ticket reopens, so subscribers undo what they did.

    Args:
        ticket: The ticket's number
        reason: Why, which subscribers see
    """
    said = reason or "the accept was undone"
    async with ctx.db.transaction() as tx:
        row = await tx.fetchrow(
            "SELECT t.id, t.queue_id, t.title, t.key, t.state, t.resolution, q.name AS queue_name FROM tickets t "
            "JOIN queues q ON q.guild_id = t.guild_id AND q.id = t.queue_id "
            "WHERE t.guild_id = $1 AND t.id = $2 FOR UPDATE OF t",
            ctx.guild.id,
            ticket,
        )
        if row is None:
            raise ActionError(f"There is no ticket {ticket} here.")
        await handled(ctx, int(row["queue_id"]), within=tx)
        if row["resolution"] != "accepted":
            raise ActionError(f"Ticket {ticket} wasn't accepted.")
        claim = await tx.fetchrow(
            "SELECT id, member, note FROM claims WHERE guild_id = $1 AND ticket_id = $2 AND state = 'accepted'",
            ctx.guild.id,
            ticket,
        )
        if claim is not None:
            await tx.execute(SETTLE, ctx.guild.id, claim["id"], "revoked", ctx.actor.user_id, said)
        await tx.execute(
            "UPDATE tickets SET state = 'open', resolution = NULL, closed_at = NULL, updated_at = now() "
            "WHERE guild_id = $1 AND id = $2",
            ctx.guild.id,
            ticket,
        )
        await tx.emit(
            Revoked(
                queue=row["queue_id"],
                queue_name=row["queue_name"],
                ticket=ticket,
                key=row["key"],
                title=row["title"],
                claim=claim["id"] if claim else None,
                note=claim["note"] if claim else None,
                claimant=claim["member"] if claim else None,
                reason=said,
            )
        )
        await refresh_board(ctx, tx, int(row["queue_id"]))
    return f"Revoked the accept of ticket {ticket}, {row['title']}, and reopened it."


TICKETS_LOCKED = """
SELECT t.id, t.queue_id, t.title, t.body, t.key, t.state, t.resolution, q.name AS queue_name FROM tickets t
JOIN queues q ON q.guild_id = t.guild_id AND q.id = t.queue_id
WHERE t.guild_id = $1 AND t.id = ANY($2::bigint[]) ORDER BY t.id FOR UPDATE OF t
"""


@plugin.action()
async def grant(
    ctx: Ctx,
    tickets: ClaimIds,
    members: Annotated[list[MemberRef], Field(min_length=1, max_length=10)],
    reason: Reason = "",
) -> str:
    """Give tickets to members who didn't claim them, such as links made before the queue, up to ten, each ticket
    paired with the member in the same place. Each closes as accepted, as an accept does.

    Args:
        tickets: The tickets' numbers
        members: Who gets each ticket, in the same order; a member who has left the server may be given one
        reason: Why, for the record
    """
    if len(tickets) != len(members):
        raise ActionError("Give one member for each ticket.")
    if len(set(tickets)) != len(tickets):
        raise ActionError("Each ticket appears once in a call.")
    given = dict(zip(tickets, members, strict=True))
    async with ctx.db.transaction() as tx:
        rows = await tx.fetch(TICKETS_LOCKED, ctx.guild.id, sorted(given))
        missing = sorted(set(given) - {int(r["id"]) for r in rows})
        if missing:
            raise ActionError(f"There is no ticket {missing[0]} here.")
        member = await member_of(ctx)
        for queue_id in sorted({int(r["queue_id"]) for r in rows}):
            queue = await queue_of(ctx, queue_id, within=tx)
            if not handles(ctx, member, queue):
                raise ActionError(f"Only {queue.name}'s handlers may grant its tickets.")
        for r in rows:
            if r["resolution"] == "accepted":
                raise ActionError(f"Ticket {r['id']}, {r['title']}, is accepted already. Revoke it first.")
        for r in rows:
            await _grant_one(ctx, tx, r, given[int(r["id"])], reason)
        for queue_id in sorted({int(r["queue_id"]) for r in rows}):
            await refresh_board(ctx, tx, queue_id)
    named = [f"{r['title']} to <@{given[int(r['id'])]}>" for r in rows]
    return f"Granted {', '.join(named)}."


async def _grant_one(ctx: Ctx, tx: Any, row: Any, to: int, reason: str) -> None:
    """Accept the member's waiting claim on a locked ticket, or record one accepted for them, and close it."""
    ticket = int(row["id"])
    own = await tx.fetchval(
        "SELECT id FROM claims WHERE guild_id = $1 AND ticket_id = $2 AND member = $3 AND state = 'waiting'",
        ctx.guild.id,
        ticket,
        to,
    )
    if own is None:
        own = await tx.fetchval(
            int,
            "INSERT INTO claims (guild_id, ticket_id, member, state, settled_by, settled_at, reason) "
            "VALUES ($1, $2, $3, 'accepted', $4, now(), $5) RETURNING id",
            ctx.guild.id,
            ticket,
            to,
            ctx.actor.user_id,
            reason or None,
        )
    else:
        await tx.execute(SETTLE, ctx.guild.id, own, "accepted", ctx.actor.user_id, reason or None)
    await tx.execute(
        "UPDATE tickets SET state = 'closed', resolution = 'accepted', closed_at = now(), updated_at = now() "
        "WHERE guild_id = $1 AND id = $2",
        ctx.guild.id,
        ticket,
    )
    [claimed] = await tx.fetch(Claim, CLAIM, ctx.guild.id, [own])
    await tx.emit(Accepted(**_base(claimed)))
    await _settled_post(tx, claimed, f"Granted by <@{ctx.actor.user_id}>.")
    await reject_waiting(ctx, tx, ticket, own, "another claim was accepted")


async def closed_reason(ctx: Ctx, db: Any, ticket: int, title: str, resolution: str | None) -> str:
    """Why a closed ticket can't be claimed, in words a member understands. It never names who holds it."""
    if resolution == "accepted":
        holder = await db.fetchval(
            "SELECT member FROM claims WHERE guild_id = $1 AND ticket_id = $2 AND state = 'accepted'",
            ctx.guild.id,
            ticket,
        )
        if holder is not None and int(holder) == ctx.actor.user_id:
            return f"{title} is already linked to you, so there's nothing to claim. See yours with /tickets mine."
        return f"{title} is already linked to another member. If it's yours, ask a handler to look into it."
    return (
        f"{title} can't be claimed right now, as it isn't in the clan. If it joined recently, try again after the "
        "next sync."
    )


async def make_claim(ctx: Ctx, ticket: int, note: str) -> str:
    """Claim an open ticket for the member asking, with the queue's limits, and post it for handlers."""
    queue_id = await ctx.db.fetchval(
        "SELECT queue_id FROM tickets WHERE guild_id = $1 AND id = $2", ctx.guild.id, ticket
    )
    if queue_id is None:
        raise ActionError(f"There is no ticket {ticket} here.")
    member = await member_of(ctx)
    async with ctx.db.transaction() as tx:
        queue = await queue_of(ctx, int(queue_id), within=tx, lock=True)
        await tx.execute(LOCK_TICKETS, ctx.guild.id, [ticket])
        row = await tx.fetchrow(
            "SELECT title, body, key, state, resolution FROM tickets WHERE guild_id = $1 AND id = $2",
            ctx.guild.id,
            ticket,
        )
        assert row is not None
        if not may_claim(member, queue):
            raise ActionError(f"Claiming in {queue.name} needs one of its roles.")
        if row["state"] != "open":
            raise ActionError(await closed_reason(ctx, tx, ticket, row["title"], row["resolution"]))
        waiting = await tx.fetch(
            "SELECT member FROM claims WHERE guild_id = $1 AND ticket_id = $2 AND state = 'waiting'",
            ctx.guild.id,
            ticket,
        )
        if any(int(w["member"]) == member.id for w in waiting):
            raise ActionError(f"You already have a claim waiting on {row['title']}.")
        if len(waiting) >= WAITING_MAX:
            raise ActionError(
                f"{row['title']} has {WAITING_MAX} claims waiting already. Try again once they're settled."
            )
        today = await tx.fetchval(
            int,
            "SELECT count(*) FROM claims c JOIN tickets t ON t.guild_id = c.guild_id AND t.id = c.ticket_id "
            "WHERE c.guild_id = $1 AND t.queue_id = $2 AND c.member = $3 AND c.created_at > $4",
            ctx.guild.id,
            queue.id,
            member.id,
            ctx.now - timedelta(days=1),
        )
        if today >= queue.per_day:
            raise ActionError(
                f"You can make {queue.per_day} claims a day in {queue.name}. Try again tomorrow."
            )
        made = await tx.fetchval(
            int,
            "INSERT INTO claims (guild_id, ticket_id, member, note) VALUES ($1, $2, $3, $4) RETURNING id",
            ctx.guild.id,
            ticket,
            member.id,
            note,
        )
        await tx.execute(TOUCH, ctx.guild.id, ticket)
        if queue.handlers_channel_id is not None:
            c = Claim(
                id=made,
                ticket_id=ticket,
                member=member.id,
                note=note,
                state="waiting",
                handler_post=None,
                title=row["title"],
                body=row["body"],
                key=row["key"],
                ticket_state="open",
                queue_id=queue.id,
                queue_name=queue.name,
            )
            post = await tx.send(
                queue.handlers_channel_id,
                _handler_text(c),
                buttons=[
                    Button(accept.bind(claims=[made]), label="Accept", style="success"),
                    Button(reject.bind(claims=[made]), label="Reject", style="danger"),
                ],
            )
            await tx.execute(
                "UPDATE claims SET handler_post = $3 WHERE guild_id = $1 AND id = $2",
                ctx.guild.id,
                made,
                post.id,
            )
    return f"Claimed {row['title']}. A handler settles it; see it with /tickets mine."


@plugin.action()
async def claim(ctx: Ctx, ticket: int, note: Note = "") -> str:
    """Claim a ticket that is yours, such as your account. A handler accepts or rejects it.

    Args:
        ticket: The ticket: type to search the open ones
        note: Anything a handler should know
    """
    return await make_claim(ctx, ticket, note)


@plugin.action()
async def withdraw(ctx: Ctx, claim: int) -> str:
    """Withdraw a claim of yours that is still waiting.

    Args:
        claim: The claim's number, as /tickets mine shows it
    """
    async with ctx.db.transaction() as tx:
        [c] = await _locked(ctx, tx, [claim])
        if c.member != ctx.actor.user_id or c.state != "waiting":
            raise ActionError(f"Claim {claim} isn't one of yours that is waiting.")
        await tx.execute(SETTLE, ctx.guild.id, c.id, "withdrawn", ctx.actor.user_id, None)
        await tx.execute(TOUCH, ctx.guild.id, c.ticket_id)
        await _settled_post(tx, c, "Withdrawn by the claimant.")
    return f"Withdrew your claim on {c.title}."


class Mine(BaseModel):
    id: int
    title: UntrustedText
    queue_name: UntrustedText
    state: str
    reason: UntrustedText | None

    def render(self) -> str:
        said = f": {self.reason}" if self.reason else ""
        return f"Claim {self.id} on {self.title} in {self.queue_name}: {self.state}{said}"


@plugin.action()
async def mine(ctx: Ctx) -> list[Mine]:
    """Your latest claims and how they stand."""
    return await ctx.db.fetch(
        Mine,
        "SELECT c.id, t.title, q.name AS queue_name, c.state, c.reason FROM claims c "
        "JOIN tickets t ON t.guild_id = c.guild_id AND t.id = c.ticket_id "
        "JOIN queues q ON q.guild_id = t.guild_id AND q.id = t.queue_id "
        "WHERE c.guild_id = $1 AND c.member = $2 ORDER BY c.id DESC LIMIT 25",
        ctx.guild.id,
        ctx.actor.user_id,
    )


class WaitingClaim(BaseModel):
    id: int
    ticket_id: int
    title: UntrustedText
    body: UntrustedText
    member: MemberRef
    note: UntrustedText
    competing: int

    def render(self) -> str:
        rival = f", {self.competing} other claims" if self.competing else ""
        return f"Claim {self.id}: <@{self.member}> on {self.title} (ticket {self.ticket_id}{rival})"


@plugin.action()
async def waiting(ctx: Ctx, queue: int) -> list[WaitingClaim]:
    """A queue's waiting claims, oldest first, for its handlers, with competing claims side by side.

    Args:
        queue: The queue's number
    """
    await handled(ctx, queue)
    return await ctx.db.fetch(
        WaitingClaim,
        """
        SELECT c.id, c.ticket_id, t.title, t.body, c.member, c.note,
               (SELECT count(*) FROM claims o WHERE o.guild_id = c.guild_id AND o.ticket_id = c.ticket_id
                AND o.state = 'waiting' AND o.id <> c.id) AS competing
        FROM claims c JOIN tickets t ON t.guild_id = c.guild_id AND t.id = c.ticket_id
        WHERE c.guild_id = $1 AND t.queue_id = $2 AND c.state = 'waiting'
        ORDER BY c.ticket_id, c.id LIMIT 50
        """,
        ctx.guild.id,
        queue,
    )


def _normal(text: str) -> str:
    return "".join(text.lower().split())


@claim.autocomplete("ticket")
async def open_tickets(ctx: Ctx, typed: str) -> list[Choice[int]]:
    """Open tickets in the queues the member may claim in, whose titles hold what they typed."""
    member = await member_of(ctx)
    rows = await ctx.db.fetch(
        "SELECT t.id, t.title, q.name, q.claimers FROM tickets t "
        "JOIN queues q ON q.guild_id = t.guild_id AND q.id = t.queue_id "
        "WHERE t.guild_id = $1 AND t.state = 'open' "
        "AND replace(lower(t.title), ' ', '') LIKE '%' || $2 || '%' ORDER BY length(t.title), t.id LIMIT 100",
        ctx.guild.id,
        _normal(typed).replace("%", "").replace("_", ""),
    )
    allowed = [r for r in rows if not r["claimers"] or member.role_ids & set(r["claimers"])]
    return [Choice(f"{r['title']} ({r['name']})"[:100], int(r["id"])) for r in allowed][:25]
