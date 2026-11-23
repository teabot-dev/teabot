"""teabot-ballots. Imports only `teabot.sdk`.

- `ballots.hello` records a member's greeting in the plugin's own table and says how many they have made.
"""

from typing import Annotated

from pydantic import BaseModel, Field
from teabot.sdk import Context, EmptyConfig, Plugin

plugin = Plugin("ballots")
type Ctx = Context[EmptyConfig]


class Greeted(BaseModel):
    times: int

    def render(self) -> str:
        return f"Hello! That is hello number {self.times}."


@plugin.action()
async def hello(ctx: Ctx, greeting: Annotated[str, Field(max_length=100)] = "hello") -> Greeted:
    """Say hello.

    Args:
        greeting: What to say
    """
    async with ctx.db.transaction() as tx:
        await tx.execute(
            "INSERT INTO greetings (guild_id, member_id, greeting) VALUES ($1, $2, $3)",
            ctx.guild.id,
            ctx.actor.user_id,
            greeting,
        )
        times = await tx.fetchval(
            int,
            "SELECT count(*) FROM greetings WHERE guild_id = $1 AND member_id = $2",
            ctx.guild.id,
            ctx.actor.user_id,
        )
    return Greeted(times=times)
