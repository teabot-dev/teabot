"""Discord fakes for plugin and core tests (D45): a guild, its roles, members and channels, and the bot itself.

They carry discord.py's own attribute and method names, which the SDK's views call, and record what was done to
them, so a test checks the outcome. Like Discord, they refuse what the bot may not do: a role at or above the
bot's own, a managed role, or a member who outranks the bot.
"""

import itertools
import mimetypes
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import discord

from teabot.sdk._clock import CLOCK
from teabot.sdk._views import MessageView, TextChannelView
from teabot.sdk.guild import ReceivedMessage
from teabot.sdk.permissions import Permission
from teabot.sdk.polls import SentPoll

_ids = itertools.count(900_000_000_000_000_000)
BOT_POSITION = 100


def next_id() -> int:
    """A fresh snowflake-sized id."""
    return next(_ids)


class _ForbiddenResponse:
    status = 403
    reason = "Forbidden"


class _NotFoundResponse:
    status = 404
    reason = "Not Found"


def forbidden(text: str = "Missing Permissions") -> discord.Forbidden:
    return discord.Forbidden(cast(Any, _ForbiddenResponse()), text)


def not_found(text: str, *, code: int = 0) -> discord.NotFound:
    error = discord.NotFound(cast(Any, _NotFoundResponse()), text)
    error.code = code
    return error


class _BadRequestResponse:
    status = 400
    reason = "Bad Request"


def bad_request(text: str) -> discord.HTTPException:
    return discord.HTTPException(cast(Any, _BadRequestResponse()), text)


@dataclass
class FakePollAnswer:
    """One answer of a fake poll, and who voted for it."""

    id: int
    text: str
    voters: list[int] = field(default_factory=list[int])

    @property
    def vote_count(self) -> int:
        return len(self.voters)


@dataclass
class FakePoll:
    """A poll on a fake message, read as discord.py's `Poll` is. It ends at `expires_at`, by the test clock, and is
    finalised once ended, unless `harness.end_poll(..., final=False)` says otherwise."""

    question: str
    answers: list[FakePollAnswer]
    multiple: bool
    expires_at: datetime
    finalised: bool | None = None

    @property
    def ended(self) -> bool:
        return self.expires_at <= CLOCK.now()

    def is_finalised(self) -> bool:
        return self.finalised if self.finalised is not None else self.ended

    def vote(self, member_id: int, text: str, *, remove: bool = False) -> None:
        """Add `member_id`'s vote for the answer `text`, or take it off. In a single-choice poll a second vote moves
        the first, as in Discord."""
        answer = next((a for a in self.answers if a.text == text), None)
        if answer is None:
            raise ValueError(f"The poll has no answer {text!r}.")
        if self.ended:
            raise ValueError("The poll has ended.")
        if not remove and not self.multiple:
            for other in self.answers:
                other.voters = [v for v in other.voters if v != member_id]
        answer.voters = [v for v in answer.voters if v != member_id]
        if not remove:
            answer.voters.append(member_id)

    def end(self, *, final: bool = True) -> None:
        if not self.ended:
            self.expires_at = CLOCK.now()
        self.finalised = final


def fake_poll(poll: Any) -> FakePoll:
    """discord.py's `Poll`, as the views send it, or the SDK's, when a test sends to a fake directly."""
    texts = [str(a.text) for a in poll.answers] if isinstance(poll, discord.Poll) else list(poll.answers)
    answers = [FakePollAnswer(n, text) for n, text in enumerate(texts, start=1)]
    return FakePoll(str(poll.question), answers, bool(poll.multiple), CLOCK.now() + poll.duration)


@dataclass
class FakeAttachment:
    filename: str
    data: bytes

    @property
    def url(self) -> str:
        return f"https://cdn.discordapp.com/attachments/0/0/{self.filename}"

    @property
    def content_type(self) -> str:
        return mimetypes.guess_type(self.filename)[0] or ""


@dataclass
class FakeReaction:
    """One emoji's reactions on a fake message: who gave them, oldest first, as discord.py's `Reaction` lists
    them."""

    emoji: str
    givers: list["FakeMember"] = field(default_factory=list["FakeMember"])

    async def users(self) -> AsyncIterator["FakeMember"]:
        for giver in self.givers:
            yield giver


@dataclass
class FakeButton:
    """A button on a fake message, as Discord shows it. `harness.press` presses it."""

    label: str | None
    emoji: str | None
    style: str
    custom_id: str


@dataclass
class FakeSelect:
    """A select menu on a fake message. `harness.choose` chooses from it. `options` are the values of a menu of
    options, empty for Discord's own role, channel and member pickers."""

    custom_id: str
    kind: str
    options: list[str]
    min: int
    max: int
    defaults: list[int] = field(default_factory=list[int])


def _buttons(view: Any = None, buttons: Sequence[Any] = ()) -> list[FakeButton]:
    """discord.py's view, as the SDK's views send it, or the SDK's `Button`s, when a test uses a fake directly."""
    if view is not None:
        return [
            FakeButton(
                item.label, str(item.emoji) if item.emoji else None, item.style.name, str(item.custom_id)
            )
            for item in view.children
            if isinstance(item, discord.ui.Button)
        ]
    return [FakeButton(b.label, b.emoji, b.style, b.bound.custom_id()) for b in buttons]


SELECT_KINDS = {"RoleSelect": "role", "ChannelSelect": "channel", "UserSelect": "member", "Select": "options"}


def _select(view: Any = None, select: Any = None) -> FakeSelect | None:
    """The select menu of discord.py's view, or of the SDK's `Select`, when a test uses a fake directly."""
    if view is not None:
        for item in view.children:
            kind = SELECT_KINDS.get(type(item).__name__)
            if kind is not None:
                values = [o.value for o in getattr(item, "options", [])]
                defaults = [int(d.id) for d in getattr(item, "default_values", [])]
                return FakeSelect(
                    str(item.custom_id), kind, values, item.min_values, item.max_values, defaults
                )
        return None
    if select is None:
        return None
    values = [o.value for o in select.options]
    return FakeSelect(
        select.bound.custom_id(select.into),
        select.kind,
        values,
        select.min,
        select.max,
        list(select.defaults),
    )


@dataclass
class _Author:
    id: int


@dataclass
class FakeMessage:
    """A message a fake channel was sent, as it shows now: its `content`, `embed`, `attachments`, `buttons` and
    `select`; how many `edits` it has had; and whether it has been `deleted`."""

    id: int
    channel_id: int
    content: str | None
    embed: discord.Embed | None
    allowed_mentions: discord.AllowedMentions | None = None
    attachments: list[FakeAttachment] = field(default_factory=list[FakeAttachment])
    buttons: list[FakeButton] = field(default_factory=list[FakeButton])
    author_id: int = 0
    select: FakeSelect | None = None
    deleted: bool = False
    edits: int = 0
    reactions: list[FakeReaction] = field(default_factory=list[FakeReaction])
    poll: FakePoll | None = None

    def react(self, member: "FakeMember", emoji: str, *, remove: bool = False) -> None:
        """Add `member`'s reaction, or take it off, as Discord records it; `harness.react` also fires the event."""
        reaction = next((r for r in self.reactions if r.emoji == emoji), None)
        if reaction is None:
            reaction = FakeReaction(emoji)
            self.reactions.append(reaction)
        reaction.givers = [g for g in reaction.givers if g.id != member.id]
        if not remove:
            reaction.givers.append(member)
        self.reactions = [r for r in self.reactions if r.givers]

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/0/{self.channel_id}/{self.id}"

    @property
    def author(self) -> _Author:
        return _Author(self.author_id)

    @property
    def poll_ends_at(self) -> datetime | None:
        return self.poll.expires_at if self.poll is not None else None

    async def end_poll(self) -> None:
        """End the message's poll, as discord.py's `end_poll` does; Discord refuses one already ended."""
        if self.deleted:
            raise not_found("Unknown Message")
        if self.poll is None or self.poll.ended:
            raise bad_request("Poll expired")
        self.poll.end()

    async def edit(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: Any = None,
        attachments: Sequence[Any] | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        buttons: Sequence[Any] = (),
        files: Sequence[Any] = (),
        select: Any = None,
    ) -> None:
        if self.deleted:
            raise not_found("Unknown Message")
        self.content, self.embed = content, embed
        self.buttons = _buttons(view, buttons)
        self.select = _select(view, select)
        self.attachments = fake_attachments(attachments or files)
        self.edits += 1

    async def delete(self) -> None:
        if self.deleted:
            raise not_found("Unknown Message")
        self.deleted = True


def fake_attachments(files: Sequence[Any] | None) -> list[FakeAttachment]:
    """discord.py's `File`s, as the views send them, or the SDK's, when a test sends to a fake directly."""
    out: list[FakeAttachment] = []
    for f in files or ():
        if isinstance(f, discord.File):
            buffer: Any = f.fp
            out.append(FakeAttachment(f.filename or "file", buffer.read()))
        else:
            out.append(FakeAttachment(str(f.name), bytes(f.data)))
    return out


@dataclass
class _GuildChannel:
    """What every channel has: an id, a name, its category, and the members who cannot see it."""

    id: int = field(default_factory=next_id)
    name: str = "channel"
    hidden_from: set[int] = field(default_factory=set[int])
    category: "FakeCategory | None" = None
    guild: "FakeGuild | None" = None
    nsfw: bool = False

    def is_nsfw(self) -> bool:
        return self.nsfw

    @property
    def category_id(self) -> int | None:
        return self.category.id if self.category is not None else None

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    def permissions_for(self, member: "FakeMember", /) -> discord.Permissions:
        return discord.Permissions(view_channel=member.id not in self.hidden_from)


@dataclass
class _PartialMessage:
    """A message by ID alone, as discord.py's `get_partial_message` gives, for one no fake channel holds."""

    id: int

    async def end_poll(self) -> None:
        raise not_found("Unknown Message")


@dataclass
class FakeChannel(_GuildChannel):
    """A text channel that records what it is sent. A `forbidden` one refuses, as Discord does when the
    bot lacks permission there. A member's permissions here are their server's, with View Channel unless the
    channel is `hidden_from` them, less any named in `refused`, such as `send_polls`."""

    sent: list[FakeMessage] = field(default_factory=list[FakeMessage])
    forbidden: bool = False
    type: str = "text"
    refused: set[str] = field(default_factory=set[str])

    def permissions_for(self, member: "FakeMember", /) -> discord.Permissions:
        held = member.guild_permissions
        allowed = discord.Permissions.all() if held.administrator else discord.Permissions(held.value)
        allowed.update(view_channel=member.id not in self.hidden_from, administrator=False)
        allowed.update(**dict.fromkeys(self.refused, False))
        return allowed

    def _bot_blind(self) -> bool:
        me = self.guild.me if self.guild is not None else None
        return me is not None and (me.id in self.hidden_from or "read_message_history" in self.refused)

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        files: Sequence[Any] | None = None,
        view: Any = None,
        ping: Iterable[int] = (),
        buttons: Sequence[Any] = (),
        select: Any = None,
        nonce: str | int | None = None,
        poll: Any = None,
        ping_roles: Iterable[int] = (),
    ) -> FakeMessage:
        """Record the message. `allowed_mentions` and `view` are discord.py's forms, which the core uses; `ping`
        and `buttons` are the SDK's, so a fake channel can stand where an action takes a `TextChannel`. A `poll`
        is either's, and `ping_roles` goes through the SDK's own sending."""
        if ping_roles:
            view = TextChannelView(self, self.guild)
            sent = await view.send(content, embed=embed, ping=ping, ping_roles=ping_roles, poll=poll)
            return cast(FakeMessage, cast(MessageView, sent).raw)
        if ping and allowed_mentions is None:
            allowed_mentions = discord.AllowedMentions(users=[discord.Object(id=i) for i in ping])
        if self.forbidden:
            raise forbidden()
        author = self.guild.me.id if self.guild is not None else 0
        message = FakeMessage(
            next_id(),
            self.id,
            content,
            embed,
            allowed_mentions,
            fake_attachments(files),
            _buttons(view, buttons),
            author,
            select=_select(view, select),
            poll=fake_poll(poll) if poll is not None else None,
        )
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int, /) -> FakeMessage:
        if self._bot_blind():
            raise forbidden("Missing Access")
        found = next((m for m in self.sent if m.id == message_id and not m.deleted), None)
        if found is None:
            raise not_found("Unknown Message")
        return found

    def get_partial_message(self, message_id: int, /) -> "FakeMessage | _PartialMessage":
        found = next((m for m in self.sent if m.id == message_id and not m.deleted), None)
        return found if found is not None else _PartialMessage(message_id)

    async def get_message(self, message_id: int, /) -> FakeMessage | None:
        """The SDK's name, for a test that uses a fake channel directly."""
        return next((m for m in self.sent if m.id == message_id and not m.deleted), None)

    async def read_message(self, message_id: int, /) -> "ReceivedMessage | None":
        """The SDK's reading of any message, for a test that uses a fake channel directly."""
        return await TextChannelView(self).read_message(message_id)

    async def reactors(self, message_id: int, emoji: str, /) -> tuple[int, ...]:
        """The SDK's list of who reacted, for a test that uses a fake channel directly."""
        return await TextChannelView(self).reactors(message_id, emoji)

    def can_mention(self, role: Any, /, *, by: Any = None) -> str | None:
        """The SDK's role-ping check, for a test that uses a fake channel directly."""
        return TextChannelView(self, self.guild).can_mention(role, by=by)

    async def read_poll(self, message_id: int, /) -> "SentPoll | None":
        """The SDK's reading of a poll, for a test that uses a fake channel directly."""
        return await TextChannelView(self, self.guild).read_poll(message_id)

    async def end_poll(self, message_id: int, /) -> None:
        """The SDK's ending of a poll, for a test that uses a fake channel directly."""
        await TextChannelView(self, self.guild).end_poll(message_id)


@dataclass
class FakeVoiceChannel(_GuildChannel):
    """A voice channel. A member is in it when their `voice_channel` is it, and the bot once it has joined.
    `refuse_connect` and `refuse_speak` stand for the bot lacking Connect or Speak there."""

    type: str = "voice"
    refuse_connect: bool = False
    refuse_speak: bool = False

    @property
    def members(self) -> list["FakeMember"]:
        """Who is in the channel, as discord.py's `VoiceChannel.members` lists them."""
        if self.guild is None:
            return []
        everyone = [*self.guild.members.values(), self.guild.me]
        return [m for m in everyone if m.voice_channel is self]

    @property
    def listener_ids(self) -> frozenset[int]:
        """The SDK's name, so a fake channel can stand where an action takes a `VoiceChannel`."""
        return frozenset(m.id for m in self.members if not m.bot)

    def permissions_for(self, member: "FakeMember", /) -> discord.Permissions:
        return discord.Permissions(
            view_channel=member.id not in self.hidden_from,
            connect=not self.refuse_connect,
            speak=not self.refuse_speak,
        )


@dataclass
class FakeCategory(_GuildChannel):
    """A category: it groups channels and is listed among them, as in discord.py."""

    type: str = "category"


type AnyFakeChannel = FakeChannel | FakeVoiceChannel | FakeCategory


@dataclass
class FakeRole:
    """A role. `grants` is what it allows, as discord.py's `Permissions`; `permissions` gives the SDK's names."""

    id: int = field(default_factory=next_id)
    name: str = "role"
    position: int = 1
    grants: discord.Permissions = field(default_factory=discord.Permissions.none)
    colour: int = 0
    managed: bool = False
    mentionable: bool = False
    hoist: bool = False

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    @property
    def permissions(self) -> frozenset[Permission]:
        return frozenset(cast(Permission, name) for name, on in self.grants if on)

    @property
    def hoisted(self) -> bool:
        return self.hoist


@dataclass
class _FakeAsset:
    url: str


@dataclass(frozen=True)
class _FakeVoiceState:
    channel: "FakeVoiceChannel"


@dataclass
class FakeMember:
    """A member. What the bot does to them is recorded: `roles` changes, `nick`, `timed_out_until`, `kicked`
    and `dms`, and every reason given, in `reasons`."""

    id: int
    guild_permissions: discord.Permissions
    name: str = "member"
    nick: str | None = None
    roles: list[FakeRole] = field(default_factory=list[FakeRole])
    bot: bool = False
    joined_at: datetime | None = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    timed_out_until: datetime | None = None
    dms_closed: bool = False
    dms: list[FakeMessage] = field(default_factory=list[FakeMessage])
    reasons: list[str | None] = field(default_factory=list[str | None])
    kicked: bool = False
    guild: "FakeGuild | None" = None
    voice_channel: "FakeVoiceChannel | None" = None

    @property
    def voice(self) -> "_FakeVoiceState | None":
        """Where the member is in voice, as discord.py's `Member.voice`; set `voice_channel` to move them."""
        return _FakeVoiceState(self.voice_channel) if self.voice_channel is not None else None

    @property
    def voice_channel_id(self) -> int | None:
        """The SDK's name, so a fake member can stand where a plugin takes a `Member`."""
        return self.voice_channel.id if self.voice_channel is not None else None

    @property
    def display_name(self) -> str:
        return self.nick or self.name

    @property
    def display_avatar(self) -> _FakeAsset:
        return _FakeAsset(f"https://cdn.discordapp.com/embed/avatars/{self.id % 6}.png")

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def role_ids(self) -> frozenset[int]:
        return frozenset(role.id for role in self.roles)

    @property
    def avatar_url(self) -> str:
        return self.display_avatar.url

    @property
    def top_role(self) -> FakeRole | None:
        return max(self.roles, key=lambda r: r.position, default=None)

    # The SDK's names, so a test can hand a fake member wherever plugin code takes a `Member`. They act at once,
    # without the checks the views make.
    async def add_role(self, role: Any, /, *, reason: str | None = None, by: Any = None) -> None:
        await self.add_roles(role, reason=reason)

    async def remove_role(self, role: Any, /, *, reason: str | None = None, by: Any = None) -> None:
        await self.remove_roles(role, reason=reason)

    async def set_nickname(self, nickname: str | None, /, *, reason: str | None = None) -> None:
        await self.edit(nick=nickname, reason=reason)

    async def remove_timeout(self, *, reason: str | None = None) -> None:
        await self.timeout(None, reason=reason)

    def _resolve(self, role: Any) -> FakeRole:
        found = self.guild.get_role(int(role.id)) if self.guild is not None else None
        if found is None:
            raise not_found("Unknown Role")
        return found

    async def add_roles(self, *roles: Any, reason: str | None = None) -> None:
        for role in roles:
            found = self._resolve(role)
            if found not in self.roles:
                self.roles.append(found)
        self.reasons.append(reason)

    async def remove_roles(self, *roles: Any, reason: str | None = None) -> None:
        ids = {int(role.id) for role in roles}
        self.roles = [r for r in self.roles if r.id not in ids]
        self.reasons.append(reason)

    async def edit(self, *, nick: str | None = None, reason: str | None = None) -> None:
        self.nick = nick
        self.reasons.append(reason)

    async def timeout(self, until: timedelta | None, /, *, reason: str | None = None) -> None:
        self.timed_out_until = None if until is None else datetime.now(UTC) + until
        self.reasons.append(reason)

    async def kick(self, *, reason: str | None = None) -> None:
        self.kicked = True
        self.reasons.append(reason)
        if self.guild is not None:
            self.guild.members.pop(self.id, None)
            self.guild.fetchable.pop(self.id, None)

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        files: Sequence[Any] | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> FakeMessage:
        if self.dms_closed:
            raise forbidden("Cannot send messages to this user")
        message = FakeMessage(next_id(), 0, content, embed, allowed_mentions, fake_attachments(files))
        self.dms.append(message)
        return message


@dataclass
class FakeUser:
    id: int
    name: str


@dataclass
class FakeBan:
    user: FakeUser
    reason: str | None


@dataclass
class FakeGuild:
    """A server. Members in `members` are cached; those in `fetchable` are only found over REST (D18).

    `me` is the bot, holding Administrator and a role at position 100, so roles a test adds sit below it unless
    it says otherwise. `owner_id` is nobody the tests add, unless one sets it.
    """

    id: int = field(default_factory=next_id)
    members: dict[int, FakeMember] = field(default_factory=dict[int, FakeMember])
    fetchable: dict[int, FakeMember] = field(default_factory=dict[int, FakeMember])
    channel_map: dict[int, AnyFakeChannel] = field(default_factory=dict[int, AnyFakeChannel])
    roles: list[FakeRole] = field(default_factory=list[FakeRole])
    name: str = "guild"
    owner_id: int = field(default_factory=next_id)
    icon: _FakeAsset | None = None
    banned: dict[int, FakeBan] = field(default_factory=dict[int, FakeBan])
    ban_reasons: list[str | None] = field(default_factory=list[str | None])
    me: FakeMember = field(init=False)

    def __post_init__(self) -> None:
        bot_role = FakeRole(name="teabot", position=BOT_POSITION, managed=True)
        self.me = FakeMember(
            next_id(), discord.Permissions(administrator=True), "teabot", roles=[bot_role], bot=True
        )
        self.me.guild = self

    @property
    def channels(self) -> list[AnyFakeChannel]:
        """Every channel, as discord.py's `Guild.channels` lists them."""
        return list(self.channel_map.values())

    @property
    def member_count(self) -> int:
        return len(self.members) + len(self.fetchable)

    async def query_members(self, query: str, *, limit: int = 5) -> list[FakeMember]:
        """Gateway member search: members whose name or nickname starts with `query`, cached or not."""
        everyone = [*self.members.values(), *self.fetchable.values()]
        q = query.lower()
        found = [m for m in everyone if m.name.lower().startswith(q) or (m.nick or "").lower().startswith(q)]
        return found[:limit]

    def get_member(self, user_id: int, /) -> FakeMember | None:
        return self.members.get(user_id)

    async def fetch_member(self, user_id: int, /) -> FakeMember:
        if user_id not in self.fetchable:
            raise not_found("Unknown Member")
        return self.fetchable[user_id]

    def get_channel(self, channel_id: int, /) -> AnyFakeChannel | None:
        return self.channel_map.get(channel_id)

    def add_member(
        self,
        permissions: discord.Permissions,
        *,
        cached: bool = True,
        name: str = "member",
        nick: str | None = None,
        roles: list[FakeRole] | None = None,
        bot: bool = False,
    ) -> FakeMember:
        member = FakeMember(next_id(), permissions, name, nick, list(roles or []), bot)
        member.guild = self
        (self.members if cached else self.fetchable)[member.id] = member
        return member

    def add_channel[C: AnyFakeChannel](self, channel: C) -> C:
        channel.guild = self
        self.channel_map[channel.id] = channel
        return channel

    def get_role(self, role_id: int, /) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)

    def add_role(
        self,
        name: str,
        *,
        position: int | None = None,
        permissions: discord.Permissions | None = None,
        managed: bool = False,
    ) -> FakeRole:
        """A role, placed above the others the test added, and below the bot's unless `position` says."""
        place = position if position is not None else max((r.position for r in self.roles), default=0) + 1
        role = FakeRole(
            name=name, position=place, grants=permissions or discord.Permissions.none(), managed=managed
        )
        self.roles.append(role)
        return role

    @property
    def _state(self) -> "_FakeState":
        """discord.py's connection state, for the role changes the SDK makes by member ID."""
        return _FakeState(_FakeHttp(self))

    async def ban(self, user: Any, *, reason: str | None = None, delete_message_seconds: int = 0) -> None:
        member = self.members.pop(int(user.id), None) or self.fetchable.pop(int(user.id), None)
        name = member.name if member is not None else "user"
        self.banned[int(user.id)] = FakeBan(FakeUser(int(user.id), name), reason)
        self.ban_reasons.append(reason)

    async def unban(self, user: Any, *, reason: str | None = None) -> None:
        if self.banned.pop(int(user.id), None) is None:
            raise not_found("Unknown Ban")
        self.ban_reasons.append(reason)

    async def bans(self, *, limit: int | None = 1000) -> AsyncIterator[FakeBan]:
        for entry in list(self.banned.values())[:limit]:
            yield entry


@dataclass(frozen=True)
class _FakeHttp:
    """The part of discord.py's HTTP client that changes a member's role by ID."""

    guild: "FakeGuild"

    def _member(self, user_id: int) -> FakeMember:
        member = self.guild.members.get(user_id) or self.guild.fetchable.get(user_id)
        if member is None:
            raise not_found("Unknown Member", code=10007)
        return member

    async def add_role(
        self, _guild_id: int, user_id: int, role_id: int, *, reason: str | None = None
    ) -> None:
        role = self.guild.get_role(role_id)
        if role is None:
            raise not_found("Unknown Role", code=10011)
        await self._member(user_id).add_roles(role, reason=reason)

    async def remove_role(
        self, _guild_id: int, user_id: int, role_id: int, *, reason: str | None = None
    ) -> None:
        role = self.guild.get_role(role_id)
        if role is None:
            raise not_found("Unknown Role", code=10011)
        await self._member(user_id).remove_roles(role, reason=reason)


@dataclass(frozen=True)
class _FakeState:
    http: _FakeHttp


@dataclass
class FakeClient:
    """The bot's view of its servers and their channels."""

    guilds: dict[int, FakeGuild] = field(default_factory=dict[int, FakeGuild])
    command_overwrites: list[tuple[int | None, list[dict[str, Any]]]] = field(
        default_factory=list[tuple[int | None, list[dict[str, Any]]]]
    )

    async def overwrite_commands(self, guild_id: int | None, commands: list[dict[str, Any]], /) -> None:
        """Record a bulk overwrite: the global commands when `guild_id` is None, else one server's."""
        self.command_overwrites.append((guild_id, commands))

    def get_guild(self, guild_id: int, /) -> FakeGuild | None:
        return self.guilds.get(guild_id)

    def get_channel(self, channel_id: int, /) -> AnyFakeChannel | None:
        return next(
            (g.channel_map[channel_id] for g in self.guilds.values() if channel_id in g.channel_map), None
        )
