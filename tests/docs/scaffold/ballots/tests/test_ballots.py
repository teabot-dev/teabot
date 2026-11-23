"""teabot-ballots, through the real loader, migrations and call path (`teabot.testing`)."""

import discord
from teabot.testing import HarnessFactory

from teabot_ballots import Greeted, hello, plugin


async def test_hello_counts_a_members_greetings(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(plugin)
    assert await harness.call(hello) == Greeted(times=1)
    assert await harness.call(hello, greeting="hi") == Greeted(times=2)


async def test_each_member_has_their_own_count(teabot_harness: HarnessFactory) -> None:
    harness = await teabot_harness(plugin)
    await harness.call(hello)
    other = harness.guild.add_member(discord.Permissions.none())
    assert await harness.as_member(other).call(hello) == Greeted(times=1)
