"""The board: one post per queue in its channel, with a Claim button and a My claims button.

The board never lists hundreds of tickets. Claim opens a form for what the member is claiming: a name that matches one
open ticket, ignoring case and spaces, claims it; otherwise the reply names the closest open tickets, and
`/tickets claim` searches them all as the member types. The count of open tickets is refreshed a minute after the
last change.
"""

import difflib
from typing import Annotated, Any

from pydantic import Field

from teabot.sdk import ActionError, Button, Surface

from . import Ctx, handled, plugin, queue_of
from .claims import closed_reason, make_claim, mine

CLOSEST = 5


def _normal(text: str) -> str:
    return "".join(text.lower().split())


def _board_text(name: str, open_count: int) -> str:
    return (
        f"**{name}**\n"
        "Press **Claim** and type what is yours. A handler accepts or rejects each claim.\n"
        f"{open_count:,} open."
    )


def _buttons(queue: int) -> list[Button]:
    return [
        Button(claim_from_board.asking(queue=queue), label="Claim", style="primary"),
        Button(mine.bind(), label="My claims"),
    ]


async def _open_count(db: Any, guild_id: int, queue: int) -> int:
    found: int = await db.fetchval(
        int,
        "SELECT count(*) FROM tickets WHERE guild_id = $1 AND queue_id = $2 AND state = 'open'",
        guild_id,
        queue,
    )
    return found


@plugin.job
async def refresh(ctx: Ctx, queue: int) -> None:
    """Show the board's count of open tickets as it is now."""
    found = await ctx.db.fetchrow(
        "SELECT name, board_post FROM queues WHERE guild_id = $1 AND id = $2", ctx.guild.id, queue
    )
    if found is None or found["board_post"] is None:
        return
    count = await _open_count(ctx.db, ctx.guild.id, queue)
    async with ctx.db.transaction() as tx:
        await tx.edit(found["board_post"], _board_text(found["name"], count), buttons=_buttons(queue))


@plugin.action()
async def post(ctx: Ctx, queue: int) -> str:
    """Post a queue's board in its channel, with a button members press to claim. Posting again replaces it.

    Args:
        queue: The queue's number
    """
    found = await handled(ctx, queue)
    count = await _open_count(ctx.db, ctx.guild.id, queue)
    async with ctx.db.transaction() as tx:
        if found.board_post is not None:
            await tx.delete(found.board_post)
        sent = await tx.send(found.channel_id, _board_text(found.name, count), buttons=_buttons(queue))
        await tx.execute(
            "UPDATE queues SET board_post = $3 WHERE guild_id = $1 AND id = $2", ctx.guild.id, queue, sent.id
        )
    return f"Posting {found.name}'s board in <#{found.channel_id}>."


@plugin.action(surfaces=Surface.COMPONENT)
async def claim_from_board(
    ctx: Ctx, queue: int, item: Annotated[str, Field(min_length=1, max_length=100)]
) -> str:
    """Claim your account.

    The board's Claim button runs this, with the name the member types. Discord shows at most 45 characters of a
    form's title and labels, so both stay short.

    Args:
        queue: The queue's number
        item: Your account's name, as in game
    """
    found = await queue_of(ctx, queue)
    every = await ctx.db.fetch(
        "SELECT id, title, state, resolution FROM tickets WHERE guild_id = $1 AND queue_id = $2",
        ctx.guild.id,
        queue,
    )
    rows = [r for r in every if r["state"] == "open"]
    wanted = _normal(item)
    exact = [r for r in rows if _normal(r["title"]) == wanted]
    if len(exact) == 1:
        return await make_claim(ctx, int(exact[0]["id"]), "")
    taken = [r for r in every if r["state"] != "open" and _normal(r["title"]) == wanted]
    if taken and not exact:
        r = taken[0]
        raise ActionError(await closed_reason(ctx, ctx.db, int(r["id"]), r["title"], r["resolution"]))
    titles = {_normal(r["title"]): r["title"] for r in rows}
    close = [titles[k] for k in difflib.get_close_matches(wanted, list(titles), n=CLOSEST, cutoff=0.6)]
    holding = [r["title"] for r in rows if wanted in _normal(r["title"])][:CLOSEST]
    named = list(dict.fromkeys([*holding, *close]))[:CLOSEST]
    if not named:
        raise ActionError(
            f"No account called {item} is in {found.name}. Check the spelling. If the account joined the clan "
            "recently, it can be claimed after the next sync."
        )
    listed = ", ".join(f"**{n}**" for n in named)
    return f"Did you mean {listed}? Claim it by its exact name, or with /tickets claim, which searches as you type."
