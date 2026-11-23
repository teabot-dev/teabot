"""The SDK's Discord types (`teabot.sdk.guild`) over discord.py's objects. Internal to the SDK.

The views read and call only discord.py's own attributes and methods, which `teabot.testing`'s fakes share, so one
implementation serves Discord and tests alike. They are the only Discord objects plugin code receives.

Each operation acts as the bot. It first checks what Discord would refuse, from the bot's highest role and its
permissions, and raises `ActionError` with the reason; a refusal Discord gives anyway becomes one too.
"""

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

import discord

from teabot.sdk._clock import now
from teabot.sdk._roles import MEMBER_GONE_CODES, MemberGone, problem, reason_text
from teabot.sdk.actions import ActionError
from teabot.sdk.components import BUTTONS_BESIDE_SELECT, BUTTONS_MAX, Button, Select
from teabot.sdk.guild import (
    Attachment,
    Ban,
    Channel,
    ChannelKind,
    File,
    Guild,
    Member,
    Message,
    ReceivedMessage,
    Role,
    TextChannel,
    VoiceChannel,
)
from teabot.sdk.permissions import Permission
from teabot.sdk.polls import MessageGone, MessageHidden, Poll, PollAnswer, SentPoll

FORBIDDEN = "I am not allowed to post in that channel."
DEFAULT_REASON = "via teabot"
MAX_TIMEOUT = timedelta(days=28)
KINDS: dict[str, ChannelKind] = {
    "text": "text",
    "voice": "voice",
    "category": "category",
    "forum": "forum",
    "stage_voice": "stage",
    "news": "announcement",
    "public_thread": "thread",
    "private_thread": "thread",
    "news_thread": "thread",
}


def _words(permission: str) -> str:
    """`manage_roles` as Discord writes it: Manage Roles."""
    return permission.replace("_", " ").title()


def _top_position(member: Any, guild: Any) -> int:
    positions = [int(r.position) for r in getattr(member, "roles", ()) if int(r.id) != int(guild.id)]
    return max(positions, default=0)


def _require(guild: Any, permission: str, doing: str) -> None:
    """Refuse unless the bot holds `permission` in the server."""
    held = guild.me.guild_permissions
    if not (held.administrator or getattr(held, permission)):
        raise ActionError(f"I need the {_words(permission)} permission to {doing}.")


def _files(files: Sequence[File]) -> list[discord.File]:
    return [discord.File(io.BytesIO(f.data), filename=f.name) for f in files]


STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


DEFAULT_KINDS = {
    "role": discord.SelectDefaultValueType.role,
    "channel": discord.SelectDefaultValueType.channel,
    "member": discord.SelectDefaultValueType.user,
}


def _menu(select: Select) -> discord.ui.Item[Any]:
    custom_id = select.bound.custom_id(select.into)
    limits: dict[str, Any] = {
        "custom_id": custom_id,
        "placeholder": select.placeholder,
        "min_values": select.min,
        "max_values": select.max,
    }
    if select.kind != "options" and select.defaults:
        kind = DEFAULT_KINDS[select.kind]
        limits["default_values"] = [discord.SelectDefaultValue(id=i, type=kind) for i in select.defaults]
    if select.kind == "role":
        return discord.ui.RoleSelect(**limits)
    if select.kind == "channel":
        return discord.ui.ChannelSelect(**limits)
    if select.kind == "member":
        return discord.ui.UserSelect(**limits)
    options = [
        discord.SelectOption(
            label=o.label, value=o.value, emoji=o.emoji, description=o.description, default=o.default
        )
        for o in select.options
    ]
    return discord.ui.Select(options=options, **limits)


def _components(buttons: Sequence[Button], select: Select | None = None) -> discord.ui.View | None:
    """discord.py's form of the buttons, five to a row, and of a select menu, which takes a row of its own. A press
    or a choice reaches the interaction router by its custom id, so the view is only ever a carrier and never
    waits for them itself."""
    if not buttons and select is None:
        return None
    room = BUTTONS_MAX if select is None else BUTTONS_BESIDE_SELECT
    if len(buttons) > room:
        raise ValueError(f"A post holds at most {room} buttons here, not {len(buttons)}.")
    view = discord.ui.View(timeout=None)
    if select is not None:
        view.add_item(_menu(select))
    for button in buttons:
        view.add_item(
            discord.ui.Button(
                label=button.label,
                emoji=button.emoji,
                style=STYLES[button.style],
                custom_id=button.bound.custom_id(),
            )
        )
    view.stop()
    return view


@dataclass(frozen=True)
class RoleView:
    raw: Any

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def name(self) -> str:
        return str(self.raw.name)

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    @property
    def position(self) -> int:
        return int(self.raw.position)

    @property
    def permissions(self) -> frozenset[Permission]:
        granted = self.raw.permissions
        if isinstance(granted, discord.Permissions):
            return frozenset(cast(Permission, name) for name, on in granted if on)
        return frozenset(granted)

    @property
    def colour(self) -> int:
        colour = self.raw.colour
        return int(getattr(colour, "value", colour))

    @property
    def managed(self) -> bool:
        return bool(self.raw.managed)

    @property
    def mentionable(self) -> bool:
        return bool(self.raw.mentionable)

    @property
    def hoisted(self) -> bool:
        return bool(self.raw.hoist)


def _roles(raw_roles: Iterable[Any], guild: Any) -> list[Role]:
    """Highest first, without @everyone, whose ID is the server's."""
    kept = [r for r in raw_roles if int(r.id) != int(guild.id)]
    return [RoleView(r) for r in sorted(kept, key=lambda r: int(r.position), reverse=True)]


@dataclass(frozen=True)
class MemberView:
    raw: Any
    guild: Any
    reason: str = DEFAULT_REASON
    action: str = ""

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def name(self) -> str:
        return str(self.raw.name)

    @property
    def display_name(self) -> str:
        return str(self.raw.display_name)

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def role_ids(self) -> frozenset[int]:
        return frozenset(int(role.id) for role in getattr(self.raw, "roles", ()))

    @property
    def roles(self) -> Sequence[Role]:
        return _roles(getattr(self.raw, "roles", ()), self.guild)

    @property
    def top_role(self) -> Role | None:
        roles = self.roles
        return roles[0] if roles else None

    @property
    def joined_at(self) -> datetime | None:
        return self.raw.joined_at

    @property
    def avatar_url(self) -> str:
        return str(self.raw.display_avatar.url)

    @property
    def bot(self) -> bool:
        return bool(self.raw.bot)

    @property
    def timed_out_until(self) -> datetime | None:
        return self.raw.timed_out_until

    @property
    def voice_channel_id(self) -> int | None:
        channel = getattr(getattr(self.raw, "voice", None), "channel", None)
        return int(channel.id) if channel is not None else None

    def _role_to_change(self, role: Role, by: Member | None = None, *, give: bool = True) -> Any:
        return role_to_change(self.guild, role, by, give=give)

    def check_outranked(self, doing: str, permission: str) -> None:
        if self.id == int(self.guild.owner_id):
            raise ActionError(f"I cannot {doing} the server's owner.")
        if _top_position(self.raw, self.guild) >= _top_position(self.guild.me, self.guild):
            raise ActionError(
                f"{self.display_name}'s highest role is at or above mine, so I cannot {doing} them."
            )
        _require(self.guild, permission, f"{doing} members")

    async def _call(self, doing: str, call: Any) -> None:
        try:
            await call
        except discord.Forbidden:
            raise ActionError(f"Discord would not let me {doing}.") from None

    async def add_role(self, role: Role, /, *, reason: str | None = None, by: Member | None = None) -> None:
        found = self._role_to_change(role, by, give=True)
        text = reason_text(self.reason, self.action, by, reason)
        await self._call(f"give {found.name}", self.raw.add_roles(found, reason=text))

    async def remove_role(
        self, role: Role, /, *, reason: str | None = None, by: Member | None = None
    ) -> None:
        found = self._role_to_change(role, by, give=False)
        text = reason_text(self.reason, self.action, by, reason)
        await self._call(f"take {found.name}", self.raw.remove_roles(found, reason=text))

    async def set_nickname(self, nickname: str | None, /, *, reason: str | None = None) -> None:
        if nickname is not None and not 1 <= len(nickname) <= 32:
            raise ActionError("A nickname is 1 to 32 characters.")
        self.check_outranked("rename", "manage_nicknames")
        await self._call("rename them", self.raw.edit(nick=nickname, reason=reason or self.reason))

    async def timeout(self, length: timedelta, /, *, reason: str | None = None) -> None:
        if not timedelta(0) < length <= MAX_TIMEOUT:
            raise ActionError("A timeout lasts more than no time, and at most 28 days.")
        self.check_outranked("time out", "moderate_members")
        await self._call("time them out", self.raw.timeout(length, reason=reason or self.reason))

    async def remove_timeout(self, *, reason: str | None = None) -> None:
        self.check_outranked("end the timeout of", "moderate_members")
        await self._call("end their timeout", self.raw.timeout(None, reason=reason or self.reason))

    async def kick(self, *, reason: str | None = None) -> None:
        self.check_outranked("kick", "kick_members")
        await self._call("kick them", self.raw.kick(reason=reason or self.reason))

    async def send(
        self, content: str | None = None, *, embed: discord.Embed | None = None, files: Sequence[File] = ()
    ) -> Message:
        try:
            sent = await self.raw.send(
                content, embed=embed, files=_files(files), allowed_mentions=allowed_mentions(())
            )
        except discord.Forbidden:
            raise ActionError(
                f"{self.display_name} does not take direct messages from this server."
            ) from None
        return MessageView(sent)


@dataclass(frozen=True)
class MessageView:
    raw: Any

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def channel_id(self) -> int:
        channel = getattr(self.raw, "channel", None)
        return int(channel.id) if channel is not None else int(self.raw.channel_id)

    @property
    def jump_url(self) -> str:
        return str(self.raw.jump_url)

    @property
    def poll_ends_at(self) -> datetime | None:
        poll = getattr(self.raw, "poll", None)
        return poll.expires_at if poll is not None else None

    async def edit(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        buttons: Sequence[Button] = (),
        files: Sequence[File] = (),
        select: Select | None = None,
    ) -> None:
        try:
            await self.raw.edit(
                content=content,
                embed=embed,
                view=_components(buttons, select),
                attachments=_files(files),
                allowed_mentions=allowed_mentions(()),
            )
        except discord.NotFound:
            raise ActionError("That message has been deleted.") from None
        except discord.Forbidden:
            raise ActionError("Discord would not let me edit that message.") from None

    async def delete(self) -> None:
        try:
            await self.raw.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            raise ActionError("Discord would not let me delete that message.") from None


def allowed_mentions(ping: Iterable[int], roles: Iterable[int] = ()) -> discord.AllowedMentions:
    """Nobody is mentioned except the members and roles named, whatever the text says."""
    users = [discord.Object(id=user_id) for user_id in ping]
    pinged: list[discord.Object] | bool = [discord.Object(id=role_id) for role_id in roles] or False
    return discord.AllowedMentions(everyone=False, users=users, roles=pinged, replied_user=False)


POLL_NEEDS = ("view_channel", "read_message_history", "send_polls")
POLL_HIDDEN = "I can no longer see that channel, or read its history, so I can't read its poll."


def _discord_poll(poll: Poll) -> discord.Poll:
    out = discord.Poll(poll.question, poll.duration, multiple=poll.multiple)
    for text in poll.answers:
        out.add_answer(text=text)
    return out


def _sent_poll(poll: Any) -> SentPoll:
    answers = tuple(PollAnswer(int(a.id), str(a.text), int(a.vote_count or 0)) for a in poll.answers)
    return SentPoll(
        str(poll.question), bool(poll.multiple), poll.expires_at, answers, bool(poll.is_finalised())
    )


@dataclass(frozen=True)
class TextChannelView:
    raw: Any
    guild: Any = None

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def name(self) -> str:
        return str(self.raw.name)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        ping: Iterable[int] = (),
        files: Sequence[File] = (),
        buttons: Sequence[Button] = (),
        select: Select | None = None,
        ping_roles: Iterable[int] = (),
        poll: Poll | None = None,
    ) -> Message:
        extra: dict[str, Any] = {}
        view = _components(buttons, select)
        if view is not None:
            extra["view"] = view
        if poll is not None:
            if view is not None:
                raise ActionError("A poll goes without buttons or a select menu.")
            self._check_poll_permissions()
            extra["poll"] = _discord_poll(poll)
        roles = list(ping_roles)
        if roles:
            mentions = " ".join(f"<@&{role_id}>" for role_id in roles)
            content = f"{mentions}\n{content}" if content else mentions
        try:
            sent = await self.raw.send(
                content,
                embed=embed,
                allowed_mentions=allowed_mentions(ping, roles),
                files=_files(files),
                **extra,
            )
        except discord.Forbidden:
            raise ActionError(FORBIDDEN) from None
        return MessageView(sent)

    def _raw_guild(self) -> Any:
        return self.guild if self.guild is not None else getattr(self.raw, "guild", None)

    def _check_poll_permissions(self) -> None:
        """Refuse a poll the bot couldn't post here, or couldn't read afterwards, naming what it lacks."""
        guild = self._raw_guild()
        if guild is None:
            return
        held = self.raw.permissions_for(guild.me)
        send = "send_messages_in_threads" if KINDS.get(_kind(self.raw)) == "thread" else "send_messages"
        missing = [p for p in (send, *POLL_NEEDS) if not getattr(held, p)]
        if missing:
            names = " and ".join(_words(p) for p in missing)
            raise ActionError(f"I need {names} in {self.mention} to post a poll there.")

    def can_mention(self, role: Role, /, *, by: Member | None = None) -> str | None:
        guild = self._raw_guild()
        found = guild.get_role(role.id) if guild is not None else None
        if guild is None or found is None:
            return "That role is not in this server."
        if int(found.id) == int(guild.id):
            return "A role ping can't reach @everyone."
        if found.mentionable:
            return None
        for who, need in ((_raw_member(guild, by), "you need"), (guild.me, "I need")):
            if who is not None and not self.raw.permissions_for(who).mention_everyone:
                return f"{found.name} can't be mentioned, and {need} Mention Everyone in {self.mention} to ping it."
        return None

    async def read_poll(self, message_id: int, /) -> SentPoll | None:
        try:
            found = await self.raw.fetch_message(message_id)
        except discord.NotFound:
            return None
        except discord.Forbidden:
            raise MessageHidden(POLL_HIDDEN) from None
        poll = getattr(found, "poll", None)
        return _sent_poll(poll) if poll is not None else None

    async def end_poll(self, message_id: int, /) -> None:
        try:
            await self.raw.get_partial_message(message_id).end_poll()
        except discord.NotFound:
            raise MessageGone("That poll's message has been deleted.") from None
        except discord.Forbidden:
            raise ActionError(
                "Discord would not let me end that poll: I can end only polls I sent."
            ) from None
        except discord.HTTPException:
            # Discord refuses to end a poll that has ended; that one is ended, as asked.
            found = await self.read_poll(message_id)
            ended = found is not None and (
                found.final or (found.ends_at is not None and found.ends_at <= now())
            )
            if not ended:
                raise ActionError("Discord would not end that poll.") from None

    async def get_message(self, message_id: int, /) -> Message | None:
        try:
            found = await self.raw.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        me = getattr(self.guild, "me", None)
        if me is not None and int(found.author.id) != int(me.id):
            return None
        return MessageView(found)

    async def read_message(self, message_id: int, /) -> ReceivedMessage | None:
        try:
            found = await self.raw.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        return ReceivedMessage(
            int(found.id),
            self.id,
            int(found.author.id),
            str(found.content or ""),
            tuple(Attachment(**a) for a in attachments(found)),
        )

    async def reactors(self, message_id: int, emoji: str, /) -> tuple[int, ...]:
        try:
            found = await self.raw.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return ()
        reaction = next((r for r in found.reactions if str(r.emoji) == emoji), None)
        if reaction is None:
            return ()
        return tuple([int(user.id) async for user in reaction.users() if not user.bot])


def attachments(message: Any) -> list[dict[str, str]]:
    """A message's files as `Attachment`'s fields, which an event's job payload carries as they are."""
    return [
        {"filename": str(a.filename), "url": str(a.url), "content_type": str(a.content_type or "")}
        for a in message.attachments
    ]


def _kind(channel: Any) -> str:
    """The fakes name their kind; discord.py gives a `ChannelType`."""
    kind = getattr(channel, "type", None)
    return str(getattr(kind, "name", kind))


def takes_messages(channel: Any) -> bool:
    """A text channel, thread or voice chat, rather than a category or forum, which have no `send`."""
    return channel is not None and callable(getattr(channel, "send", None)) and _kind(channel) != "voice"


@dataclass(frozen=True)
class ChannelView:
    raw: Any

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def name(self) -> str:
        return str(self.raw.name)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @property
    def kind(self) -> ChannelKind:
        return KINDS.get(_kind(self.raw), "other")

    @property
    def parent_id(self) -> int | None:
        parent = getattr(self.raw, "parent_id", None) or getattr(self.raw, "category_id", None)
        return int(parent) if parent is not None else None

    @property
    def nsfw(self) -> bool:
        # discord.py's channels and threads say through is_nsfw(), which for a thread asks its channel.
        is_nsfw = getattr(self.raw, "is_nsfw", None)
        return bool(is_nsfw()) if callable(is_nsfw) else False


def is_voice(channel: Any) -> bool:
    """A voice channel, which the bot can join to play audio. Not a stage, where it would join as audience."""
    return channel is not None and _kind(channel) == "voice"


@dataclass(frozen=True)
class VoiceChannelView:
    raw: Any

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def name(self) -> str:
        return str(self.raw.name)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @property
    def listener_ids(self) -> frozenset[int]:
        return frozenset(int(m.id) for m in self.raw.members if not m.bot)


@dataclass(frozen=True)
class GuildView:
    raw: Any
    reason: str = DEFAULT_REASON
    action: str = ""

    @property
    def id(self) -> int:
        return int(self.raw.id)

    @property
    def name(self) -> str:
        return str(getattr(self.raw, "name", ""))

    @property
    def icon_url(self) -> str | None:
        icon = getattr(self.raw, "icon", None)
        return str(icon.url) if icon is not None else None

    @property
    def owner_id(self) -> int:
        return int(self.raw.owner_id)

    @property
    def member_count(self) -> int:
        return int(self.raw.member_count or 0)

    @property
    def me(self) -> Member:
        return self._member(self.raw.me)

    @property
    def roles(self) -> Sequence[Role]:
        return _roles(self.raw.roles, self.raw)

    @property
    def channels(self) -> Sequence[Channel]:
        return [ChannelView(c) for c in self.raw.channels]

    def _find_channel(self, channel_id: int) -> Any:
        """A channel or a thread: discord.py's `get_channel` leaves threads out."""
        find = getattr(self.raw, "get_channel_or_thread", None) or self.raw.get_channel
        return find(channel_id)

    def get_text_channel(self, channel_id: int, /) -> TextChannel | None:
        channel = self._find_channel(channel_id)
        return TextChannelView(channel, self.raw) if takes_messages(channel) else None

    def get_channel(self, channel_id: int, /) -> Channel | None:
        channel = self._find_channel(channel_id)
        return ChannelView(channel) if channel is not None else None

    def get_voice_channel(self, channel_id: int, /) -> VoiceChannel | None:
        channel = self.raw.get_channel(channel_id)
        return VoiceChannelView(channel) if is_voice(channel) else None

    def get_role(self, role_id: int, /) -> Role | None:
        role = self.raw.get_role(role_id)
        return RoleView(role) if role is not None else None

    def _member(self, raw: Any) -> Member:
        return MemberView(raw, self.raw, self.reason, self.action)

    def can_assign(self, role: Role, /, *, by: Member | None = None, give: bool = True) -> str | None:
        found = self.raw.get_role(role.id)
        if found is None:
            return "That role is not in this server."
        return problem(self.raw, found, _raw_member(self.raw, by), give=give)

    async def add_role(
        self, user_id: int, role: Role, /, *, by: Member | None = None, reason: str | None = None
    ) -> None:
        await self._change_role(user_id, role, (True, by, reason))

    async def remove_role(
        self, user_id: int, role: Role, /, *, by: Member | None = None, reason: str | None = None
    ) -> None:
        await self._change_role(user_id, role, (False, by, reason))

    async def _change_role(
        self, user_id: int, role: Role, how: tuple[bool, Member | None, str | None]
    ) -> None:
        """Give or take a role by member ID, in one request, without fetching the member first."""
        give, by, extra = how
        found = role_to_change(self.raw, role, by, give=give)
        http = self.raw._state.http  # pyright: ignore[reportPrivateUsage]
        change = http.add_role if give else http.remove_role
        try:
            await change(
                int(self.raw.id),
                user_id,
                int(found.id),
                reason=reason_text(self.reason, self.action, by, extra),
            )
        except discord.NotFound as exc:
            if exc.code in MEMBER_GONE_CODES:
                raise MemberGone("That member is no longer in this server.") from None
            raise ActionError("That role is not in this server.") from None
        except discord.Forbidden:
            raise ActionError(
                f"Discord would not let me {'give' if give else 'take'} {found.name}."
            ) from None

    async def get_member(self, user_id: int, /) -> Member | None:
        member = self.raw.get_member(user_id)
        if member is None:
            try:
                member = await self.raw.fetch_member(user_id)
            except discord.NotFound:
                return None
        return self._member(member)

    async def find_members(self, prefix: str, /, *, limit: int = 10) -> list[Member]:
        found = await self.raw.query_members(prefix, limit=max(1, min(limit, 100)))
        return [self._member(m) for m in found]

    async def ban(self, user_id: int, /, *, reason: str | None = None, delete_message_days: int = 0) -> None:
        if not 0 <= delete_message_days <= 7:
            raise ActionError("A ban deletes messages from up to the last 7 days.")
        member = self.raw.get_member(user_id)
        if member is not None:
            MemberView(member, self.raw).check_outranked("ban", "ban_members")
        elif user_id == int(self.raw.owner_id):
            raise ActionError("I cannot ban the server's owner.")
        else:
            _require(self.raw, "ban_members", "ban members")
        try:
            await self.raw.ban(
                discord.Object(id=user_id),
                reason=reason or self.reason,
                delete_message_seconds=delete_message_days * 86400,
            )
        except discord.NotFound:
            raise ActionError("There is no Discord user with that ID.") from None
        except discord.Forbidden:
            raise ActionError("Discord would not let me ban them.") from None

    async def unban(self, user_id: int, /, *, reason: str | None = None) -> None:
        _require(self.raw, "ban_members", "lift bans")
        try:
            await self.raw.unban(discord.Object(id=user_id), reason=reason or self.reason)
        except discord.NotFound:
            raise ActionError("That user is not banned here.") from None

    async def bans(self, *, limit: int = 100) -> list[Ban]:
        _require(self.raw, "ban_members", "see the bans")
        return [Ban(int(e.user.id), str(e.user.name), e.reason) async for e in self.raw.bans(limit=limit)]


def guild_view(guild: Any, reason: str = DEFAULT_REASON, action: str = "") -> Guild:
    """The SDK's view of a server, for plugin code: discord.py's `Guild`, or a `teabot.testing` fake. `reason` goes
    to Discord's audit log with every change the plugin makes, with any reason of the plugin's after it. `action`
    names the action or job, for a change made `by=` a member."""
    return GuildView(guild, reason, action)


def _raw_member(guild: Any, by: Member | None) -> Any:
    if by is None:
        return None
    raw = getattr(by, "raw", None)
    return raw if raw is not None else guild.get_member(by.id)


def role_to_change(guild: Any, role: Role, by: Member | None, *, give: bool) -> Any:
    """The server's role, once every check for the change passes; else `ActionError` says why."""
    found = guild.get_role(role.id)
    if found is None:
        raise ActionError("That role is not in this server.")
    if why := problem(guild, found, _raw_member(guild, by), give=give):
        raise ActionError(why)
    _require(guild, "manage_roles", f"give or take {found.name}")
    return found
