"""teabot-tickets: queues of items members claim, handlers who settle the claims, and events for what follows."""

import json
from typing import Any

import discord
import pytest

from teabot.plugins.tickets import plugin, queue_add
from teabot.plugins.tickets.board import claim_from_board, post
from teabot.plugins.tickets.claims import accept, claim, grant, mine, reject, revoke, waiting, withdraw
from teabot.plugins.tickets.items import cancel, create, list_, reopen
from teabot.sdk.inputs import input_fields
from teabot.testing import FakeChannel, FakeMember, FakeRole, HarnessFactory, PluginHarness

NONE = discord.Permissions.none()


class Desk:
    """A server with a queue, "Link your account", handled by Staff, with a channel for handlers."""

    def __init__(
        self, harness: PluginHarness, staff: FakeRole, board: FakeChannel, inbox: FakeChannel
    ) -> None:
        self.harness, self.staff, self.board, self.inbox = harness, staff, board, inbox
        self.queue = 0
        self.handler = harness.guild.add_member(NONE, name="handler", roles=[staff])

    def member(self, name: str) -> FakeMember:
        return self.harness.guild.add_member(NONE, name=name)

    async def events(self) -> list[tuple[str, dict[str, Any]]]:
        db: Any = self.harness.admin
        rows = await db.fetch("SELECT name, payload FROM core.events ORDER BY id")
        return [(r["name"], json.loads(r["payload"])) for r in rows]


async def desk(teabot_harness: HarnessFactory) -> Desk:
    harness = await teabot_harness(plugin)
    staff = harness.guild.add_role("Staff")
    board = harness.guild.add_channel(FakeChannel())
    inbox = harness.guild.add_channel(FakeChannel())
    found = Desk(harness, staff, board, inbox)
    made = await harness.call(
        queue_add, name="Link your account", channel=board, handlers=[staff], handlers_channel=inbox
    )
    found.queue = int(made.split("queue ")[1].split(",")[0])
    return found


async def test_create_keeps_tickets_in_step_by_key(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    first = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=["Green Clog", "Fred"], keys=["11", "12"], bodies=["rank: ruby", ""]
    )
    assert [t.outcome for t in first.tickets] == ["created", "created"]
    again = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=["Green Clog", "Fred Two"], keys=["11", "12"], bodies=["rank: ruby", ""]
    )
    assert [t.outcome for t in again.tickets] == ["unchanged", "updated"]
    await d.harness.as_member(d.handler).call(cancel, ticket=first.tickets[0].id)
    closed = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=["Green Clog 2"], keys=["11"]
    )
    assert (closed.tickets[0].outcome, closed.tickets[0].state) == ("updated", "closed")


async def test_only_handlers_create_and_read(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    sam = d.member("sam")
    with pytest.raises(Exception, match="handlers may do that"):
        await d.harness.as_member(sam).call(create, queue=d.queue, titles=["Mine now"])
    with pytest.raises(Exception, match="handlers may do that"):
        await d.harness.as_member(sam).call(list_, queue=d.queue)


async def test_accepting_one_claim_closes_the_ticket_and_rejects_the_rest(
    teabot_harness: HarnessFactory,
) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=["Green Clog"], keys=["11"]
    )
    ticket = made.tickets[0].id
    sam, ann = d.member("sam"), d.member("ann")
    await d.harness.as_member(sam).call(claim, ticket=ticket, note="my main")
    await d.harness.as_member(ann).call(claim, ticket=ticket)
    await d.harness.send_posts()
    assert len(d.inbox.sent) == 2
    assert [b.label for b in d.inbox.sent[0].buttons] == ["Accept", "Reject"]
    claims = await d.harness.as_member(d.handler).call(waiting, queue=d.queue)
    sams = next(c for c in claims if c.member == sam.id)
    await d.harness.as_member(d.handler).call(accept, claims=[sams.id])
    assert [(n, p["claimant"]) for n, p in await d.events()] == [
        ("accepted", str(sam.id)),
        ("rejected", str(ann.id)),
    ]
    page = await d.harness.as_member(d.handler).call(list_, queue=d.queue)
    assert (page.tickets[0].state, page.tickets[0].resolution) == ("closed", "accepted")
    with pytest.raises(Exception, match="already linked to another member"):
        await d.harness.as_member(ann).call(claim, ticket=ticket)


async def test_revoking_reopens_the_ticket_and_tells_subscribers(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=["Green Clog"], keys=["11"]
    )
    ticket = made.tickets[0].id
    sam = d.member("sam")
    await d.harness.as_member(sam).call(claim, ticket=ticket)
    [c] = await d.harness.as_member(d.handler).call(waiting, queue=d.queue)
    await d.harness.as_member(d.handler).call(accept, claims=[c.id])
    with pytest.raises(Exception, match="Undo that with /tickets revoke"):
        await d.harness.as_member(d.handler).call(reopen, ticket=ticket)
    await d.harness.as_member(d.handler).call(revoke, ticket=ticket, reason="wrong member")
    name, payload = (await d.events())[-1]
    assert (name, payload["claimant"], payload["reason"]) == ("revoked", str(sam.id), "wrong member")
    assert (await d.harness.as_member(sam).call(mine))[0].state == "revoked"


async def test_a_claimant_gets_one_waiting_claim_per_ticket_and_a_daily_limit(
    teabot_harness: HarnessFactory,
) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=[f"Alt {n}" for n in range(11)]
    )
    sam = d.member("sam")
    await d.harness.as_member(sam).call(claim, ticket=made.tickets[0].id)
    with pytest.raises(Exception, match="already have a claim waiting"):
        await d.harness.as_member(sam).call(claim, ticket=made.tickets[0].id)
    for t in made.tickets[1:10]:
        await d.harness.as_member(sam).call(claim, ticket=t.id)
    with pytest.raises(Exception, match="10 claims a day"):
        await d.harness.as_member(sam).call(claim, ticket=made.tickets[10].id)
    [first, *_] = await d.harness.as_member(sam).call(mine)
    await d.harness.as_member(sam).call(withdraw, claim=first.id)


async def test_cancelling_rejects_waiting_claims(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(create, queue=d.queue, titles=["Green Clog"])
    sam = d.member("sam")
    await d.harness.as_member(sam).call(claim, ticket=made.tickets[0].id)
    await d.harness.as_member(d.handler).call(cancel, ticket=made.tickets[0].id, reason="left the clan")
    assert [(n, p["reason"]) for n, p in await d.events()] == [("rejected", "left the clan")]
    await d.harness.as_member(d.handler).call(reopen, ticket=made.tickets[0].id)


async def test_the_board_claims_by_exact_name_and_names_close_ones(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    await d.harness.as_member(d.handler).call(create, queue=d.queue, titles=["Green Clog", "Green Hat"])
    await d.harness.as_member(d.handler).call(post, queue=d.queue)
    await d.harness.send_posts()
    assert (d.board.sent[0].content or "").endswith("2 open.")
    sam = d.member("sam")
    said = await d.harness.as_member(sam).call(claim_from_board, queue=d.queue, item="gren clog")
    assert "Green Clog" in said and "Did you mean" in said
    said = await d.harness.as_member(sam).call(claim_from_board, queue=d.queue, item="greenclog")
    assert said.startswith("Claimed Green Clog.")


async def test_a_reject_leaves_the_ticket_open(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(create, queue=d.queue, titles=["Green Clog"])
    sam = d.member("sam")
    await d.harness.as_member(sam).call(claim, ticket=made.tickets[0].id)
    [c] = await d.harness.as_member(d.handler).call(waiting, queue=d.queue)
    await d.harness.as_member(d.handler).call(reject, claims=[c.id], reason="not yours")
    page = await d.harness.as_member(d.handler).call(list_, queue=d.queue)
    assert page.tickets[0].state == "open"
    assert page.tickets[0].claims[0].reason == "not yours"


async def test_grant_links_a_ticket_nobody_claimed_even_for_a_member_who_left(
    teabot_harness: HarnessFactory,
) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(
        create, queue=d.queue, titles=["Green Clog", "Old Alt"], keys=["11", "12"]
    )
    green, old = made.tickets[0].id, made.tickets[1].id
    await d.harness.as_member(d.handler).call(cancel, ticket=old)
    departed = 900_000_000_000_999_999
    sam = d.member("sam")
    await d.harness.as_member(d.handler).call(grant, tickets=[green, old], members=[sam.id, departed])
    assert [(n, p["claimant"]) for n, p in await d.events()] == [
        ("accepted", str(sam.id)),
        ("accepted", str(departed)),
    ]
    page = await d.harness.as_member(d.handler).call(list_, queue=d.queue)
    assert [(t.resolution, t.claims[0].member) for t in page.tickets] == [
        ("accepted", sam.id),
        ("accepted", departed),
    ]
    with pytest.raises(Exception, match="Revoke it first"):
        await d.harness.as_member(d.handler).call(grant, tickets=[green], members=[sam.id])


async def test_grant_accepts_the_members_own_waiting_claim_and_rejects_the_rest(
    teabot_harness: HarnessFactory,
) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(create, queue=d.queue, titles=["Green Clog"])
    ticket = made.tickets[0].id
    sam, ann = d.member("sam"), d.member("ann")
    await d.harness.as_member(sam).call(claim, ticket=ticket, note="mine")
    await d.harness.as_member(ann).call(claim, ticket=ticket)
    await d.harness.as_member(d.handler).call(grant, tickets=[ticket], members=[sam.id])
    page = await d.harness.as_member(d.handler).call(list_, queue=d.queue)
    assert sorted((c.member, c.state) for c in page.tickets[0].claims) == sorted(
        [(sam.id, "accepted"), (ann.id, "rejected")]
    )
    with pytest.raises(Exception, match="handlers may grant"):
        await d.harness.as_member(ann).call(grant, tickets=[ticket], members=[ann.id])


async def test_a_claim_on_a_linked_or_departed_account_says_why(teabot_harness: HarnessFactory) -> None:
    d = await desk(teabot_harness)
    made = await d.harness.as_member(d.handler).call(create, queue=d.queue, titles=["Green Clog", "Gone Guy"])
    green, gone = made.tickets[0].id, made.tickets[1].id
    sam, ann = d.member("sam"), d.member("ann")
    await d.harness.as_member(d.handler).call(grant, tickets=[green], members=[sam.id])
    await d.harness.as_member(d.handler).call(cancel, ticket=gone)
    with pytest.raises(Exception, match="already linked to you"):
        await d.harness.as_member(sam).call(claim_from_board, queue=d.queue, item="green clog")
    with pytest.raises(Exception, match="already linked to another member"):
        await d.harness.as_member(ann).call(claim_from_board, queue=d.queue, item="Green Clog")
    with pytest.raises(Exception, match="isn't in the clan"):
        await d.harness.as_member(ann).call(claim, ticket=gone)
    with pytest.raises(Exception, match="after the next sync"):
        await d.harness.as_member(ann).call(claim_from_board, queue=d.queue, item="Nobody Here")


def test_the_claim_form_fits_discords_45_characters() -> None:
    spec = claim_from_board.spec()
    field = next(f for f in input_fields(spec.input) if f.name == "item")
    assert len(spec.summary) <= 45 and len(field.description) <= 45
