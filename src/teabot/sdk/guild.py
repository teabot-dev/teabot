"""The parts of a Discord server a plugin works with (D45). The core gives plugins these, never discord.py's own
objects, so plugin code types the same against Discord and against `teabot.testing`'s fakes.

- `Guild`: the server, to find its channels, roles and members, and to ban.
- `TextChannel`: a channel that takes messages. Its `send` mentions nobody unless `ping` names them.
- `Channel`: any channel, of any kind, to read.
- `VoiceChannel`: a voice channel, for `ctx.voice.join` (D54).
- `Member`, `Role` and `Message`: what those lookups and sends return.
- `User`, `ReceivedMessage`, `Reaction`, `RoleChange` and `VoiceChange`: what the event handlers receive.
  `TextChannel.read_message` returns a `ReceivedMessage` too, with its `Attachment`s.

Every operation acts with the bot's own power; the plugin decides who may use it. Before calling Discord, each one
checks what Discord would refuse, such as a role above the bot's own, and raises `ActionError` with the reason in
plain words. Changes carry a reason for Discord's audit log, "via teabot: <action>" unless the plugin gives one.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

import discord

from teabot.sdk.components import Button, Select
from teabot.sdk.permissions import Permission

if TYPE_CHECKING:
    from teabot.sdk.polls import Poll, SentPoll

# Discord's IDs count milliseconds from the start of 2015 in their top bits.
DISCORD_EPOCH_MS = 1_420_070_400_000

type ChannelKind = Literal["text", "voice", "category", "forum", "stage", "thread", "announcement", "other"]


class Role(Protocol):
    """A role in the server."""

    @property
    def id(self) -> int:
        """The role's Discord ID."""
        ...

    @property
    def name(self) -> str:
        """The role's name."""
        ...

    @property
    def mention(self) -> str:
        """The role as a mention, such as `<@&123>`."""
        ...

    @property
    def position(self) -> int:
        """Its place in the role list: higher is more senior, and @everyone is 0."""
        ...

    @property
    def permissions(self) -> frozenset[Permission]:
        """The permissions it grants, by the names `needs=` uses, such as `"manage_messages"`."""
        ...

    @property
    def colour(self) -> int:
        """Its colour as an RGB number, 0 for none."""
        ...

    @property
    def managed(self) -> bool:
        """Whether Discord or an integration manages it, such as a bot's own role or the booster role. Nobody can
        give or take a managed role."""
        ...

    @property
    def mentionable(self) -> bool:
        """Whether anyone may mention it."""
        ...

    @property
    def hoisted(self) -> bool:
        """Whether its members are listed apart in the member list."""
        ...


class Member(Protocol):
    """A member of the server."""

    @property
    def id(self) -> int:
        """The member's Discord ID."""
        ...

    @property
    def name(self) -> str:
        """Their Discord username."""
        ...

    @property
    def display_name(self) -> str:
        """Their nickname in the server, or their name."""
        ...

    @property
    def mention(self) -> str:
        """The member as a mention, such as `<@123>`."""
        ...

    @property
    def role_ids(self) -> frozenset[int]:
        """The IDs of the roles they hold."""
        ...

    @property
    def roles(self) -> Sequence[Role]:
        """The roles they hold, highest first, without @everyone."""
        ...

    @property
    def top_role(self) -> Role | None:
        """Their highest role, or None when they have none."""
        ...

    @property
    def joined_at(self) -> datetime | None:
        """When they joined the server, when Discord says."""
        ...

    @property
    def avatar_url(self) -> str:
        """Their avatar in this server, or their own."""
        ...

    @property
    def bot(self) -> bool:
        """Whether the member is a bot."""
        ...

    @property
    def timed_out_until(self) -> datetime | None:
        """When their timeout ends, or None when they are not timed out."""
        ...

    @property
    def voice_channel_id(self) -> int | None:
        """The voice channel the member is in now, or None when they are in none."""
        ...

    async def add_role(self, role: Role, /, *, reason: str | None = None, by: "Member | None" = None) -> None:
        """Give them the role. Refused for a managed role, @everyone, or a role at or above the bot's own.

        With `by`, the member the change is made for, it is refused too unless `by` owns the server, has Manage
        Roles, ranks above the role, and holds every permission the role grants. Discord's audit log then names
        `by`. A `reason` follows teabot's own part of the audit log's text."""
        ...

    async def remove_role(
        self, role: Role, /, *, reason: str | None = None, by: "Member | None" = None
    ) -> None:
        """Take the role from them, with the same limits as `add_role`, except that `by` need not hold the role's
        permissions, since taking a role grants nothing."""
        ...

    async def set_nickname(self, nickname: str | None, /, *, reason: str | None = None) -> None:
        """Set their nickname, or clear it with None. Refused for the owner, or a member whose highest role is at
        or above the bot's."""
        ...

    async def timeout(self, length: timedelta, /, *, reason: str | None = None) -> None:
        """Stop them talking for `length`, up to 28 days, with the same limits as `set_nickname`."""
        ...

    async def remove_timeout(self, *, reason: str | None = None) -> None:
        """End their timeout."""
        ...

    async def kick(self, *, reason: str | None = None) -> None:
        """Remove them from the server. They can join again with an invite."""
        ...

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        files: Sequence["File"] = (),
    ) -> "Message":
        """Send them a direct message. Raises `ActionError` when they do not take messages from this server."""
        ...


@dataclass(frozen=True)
class File:
    """A file to attach to a message.

    - `name` is what it is called in Discord, such as `results.csv`.
    - `data` is its contents.
    """

    name: str
    data: bytes


@dataclass(frozen=True)
class User:
    """Someone who is no longer a member, such as one who has just left.

    - `id` is their Discord ID, and `name` their Discord username as it was then.
    - `mention` is them as a mention, which Discord shows by name while they can see the server's members.
    """

    id: int
    name: str

    @property
    def mention(self) -> str:
        """The user as a mention, such as `<@123>`."""
        return f"<@{self.id}>"


@dataclass(frozen=True)
class Attachment:
    """A file on a message someone posted.

    - `filename` is its name, and `url` where Discord serves it.
    - `content_type` is its media type, such as `image/png`, or empty when Discord does not say.
    """

    filename: str
    url: str
    content_type: str = ""

    @property
    def image(self) -> bool:
        """Whether it is a picture, which an embed can show."""
        return self.content_type.startswith("image/")


@dataclass(frozen=True)
class ReceivedMessage:
    """A message someone posted, as `@plugin.on_message` receives it and `TextChannel.read_message` reads it.

    - `id`, `channel_id` and `author_id` are its Discord IDs, its channel's and its author's.
    - `content` is its text, and `attachments` its files. Discord sends them only when the operator turns on the
      Message Content intent (`TEABOT_MESSAGE_CONTENT=true`); otherwise they are empty, except in messages that
      mention the bot.
    """

    id: int
    channel_id: int
    author_id: int
    content: str
    attachments: tuple[Attachment, ...] = ()

    @property
    def created_at(self) -> datetime:
        """When it was posted, which its ID records."""
        return datetime.fromtimestamp(((self.id >> 22) + DISCORD_EPOCH_MS) / 1000, UTC)

    @property
    def jump_url(self) -> str:
        """A link to the message, for any server: Discord finds the server from the channel."""
        return f"https://discord.com/channels/@me/{self.channel_id}/{self.id}"


@dataclass(frozen=True)
class Reaction:
    """A reaction added to or taken off a message.

    - `message_id` and `channel_id` say which message, and `user_id` who reacted.
    - `emoji` is the emoji as text: the character, such as `"👍"`, or a server emoji as Discord writes it,
      `"<:raids:123>"`.
    """

    message_id: int
    channel_id: int
    user_id: int
    emoji: str


@dataclass(frozen=True)
class RoleChange:
    """A member's roles changing.

    - `member` is the member, as they are now.
    - `added` and `removed` are the IDs of the roles they gained and lost.
    """

    member: Member
    added: tuple[int, ...]
    removed: tuple[int, ...]


@dataclass(frozen=True)
class VoiceChange:
    """A member moving between voice channels.

    - `member` is who moved, as they are now.
    - `left` is the ID of the channel they left, and `joined` the one they joined. Joining has `left` None,
      leaving voice has `joined` None, and moving has both.
    """

    member: Member
    left: int | None
    joined: int | None


@dataclass(frozen=True)
class Ban:
    """A ban in the server.

    - `user_id` and `name` are the banned user's ID and Discord username.
    - `reason` is the reason given, if any.
    """

    user_id: int
    name: str
    reason: str | None


class Message(Protocol):
    """A message the bot sent."""

    @property
    def id(self) -> int:
        """The message's Discord ID."""
        ...

    @property
    def channel_id(self) -> int:
        """The ID of the channel it is in."""
        ...

    @property
    def jump_url(self) -> str:
        """A link to the message."""
        ...

    @property
    def poll_ends_at(self) -> datetime | None:
        """When Discord ends the message's poll, or None for a message with no poll."""
        ...

    async def edit(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        buttons: Sequence[Button] = (),
        files: Sequence[File] = (),
        select: Select | None = None,
    ) -> None:
        """Replace the message's text, embed, buttons, select menu and files with these: what is given is what it
        shows."""
        ...

    async def delete(self) -> None:
        """Delete the message. Deleting one already gone does nothing."""
        ...


class Channel(Protocol):
    """Any channel in the server, to read. Post through `TextChannel`, from `Guild.get_text_channel`."""

    @property
    def id(self) -> int:
        """The channel's Discord ID."""
        ...

    @property
    def name(self) -> str:
        """The channel's name."""
        ...

    @property
    def mention(self) -> str:
        """The channel as a link, such as `<#123>`."""
        ...

    @property
    def kind(self) -> ChannelKind:
        """What sort of channel it is."""
        ...

    @property
    def parent_id(self) -> int | None:
        """Its category, or for a thread the channel it is in; None when it has neither."""
        ...

    @property
    def nsfw(self) -> bool:
        """Whether it is age-restricted. A thread is when the channel it is in is."""
        ...


class TextChannel(Protocol):
    """A channel that takes messages."""

    @property
    def id(self) -> int:
        """The channel's Discord ID."""
        ...

    @property
    def name(self) -> str:
        """The channel's name."""
        ...

    @property
    def mention(self) -> str:
        """The channel as a link, such as `<#123>`."""
        ...

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
        poll: "Poll | None" = None,
    ) -> Message:
        """Post a message. Nobody is mentioned, whatever the text says, except the members whose IDs are in
        `ping`, and the roles whose IDs are in `ping_roles`, whose mentions are written before the content. Check
        a role ping first with `can_mention`. Up to 25 `buttons` go under it, five to a row, or a `select` menu and
        up to 20 buttons. Raises `ActionError` when the bot may not post here.

        With a `poll`, Discord's own poll goes with the message, and no buttons or menu may. The bot's permissions
        are checked first: Send Messages, or Send Messages in Threads in a thread, Send Polls, and View Channel and
        Read Message History, so the poll can be read later. A missing one raises `ActionError` naming it."""
        ...

    def can_mention(self, role: "Role", /, *, by: "Member | None" = None) -> str | None:
        """Why a ping of `role` in this channel wouldn't work, or None when it would. `by` may ask for it when the
        role is mentionable or they have Mention Everyone here; the bot can deliver it when the role is
        mentionable or the bot has Mention Everyone here, and otherwise Discord drops it without a word."""
        ...

    async def read_poll(self, message_id: int, /) -> "SentPoll | None":
        """A message's poll as Discord shows it now, anyone's, or None when the message is deleted or has no poll.
        Raises `MessageHidden` when the bot can no longer see this channel or read its history."""
        ...

    async def end_poll(self, message_id: int, /) -> None:
        """End a poll the bot sent, by its message's ID. Discord then finalises it, as when time runs out. Ending
        one that has ended already does nothing; one whose message is deleted raises `MessageGone`."""
        ...

    async def get_message(self, message_id: int, /) -> Message | None:
        """One of the bot's own messages in this channel, to edit or delete, or None when it is gone or is not
        the bot's."""
        ...

    async def read_message(self, message_id: int, /) -> ReceivedMessage | None:
        """Any message in this channel, anyone's, to read, or None when it is gone or the bot cannot see it. Its
        text and files need the Message Content intent, as `on_message`'s do."""
        ...

    async def reactors(self, message_id: int, emoji: str, /) -> tuple[int, ...]:
        """The IDs of the people who reacted to a message in this channel with `emoji`, bots left out, oldest
        first: none when the message is gone or nobody has. `emoji` is written as `Reaction.emoji` is."""
        ...


class VoiceChannel(Protocol):
    """A voice channel, which the bot can join to play audio (D54). A stage is not one: on a stage the bot would
    join as an audience member, and nobody would hear it."""

    @property
    def id(self) -> int:
        """The channel's Discord ID."""
        ...

    @property
    def name(self) -> str:
        """The channel's name."""
        ...

    @property
    def mention(self) -> str:
        """The channel as a link, such as `<#123>`."""
        ...

    @property
    def listener_ids(self) -> frozenset[int]:
        """The members in the channel now, other than bots, the bot itself included."""
        ...


class Guild(Protocol):
    """The server an action or job is about."""

    @property
    def id(self) -> int:
        """The server's Discord ID."""
        ...

    @property
    def name(self) -> str:
        """The server's name."""
        ...

    @property
    def icon_url(self) -> str | None:
        """The server's icon, or None when it has none."""
        ...

    @property
    def owner_id(self) -> int:
        """The ID of the server's owner."""
        ...

    @property
    def me(self) -> Member:
        """The bot itself, as a member, to compare roles with: it can manage only roles below its highest."""
        ...

    @property
    def member_count(self) -> int:
        """How many members the server has."""
        ...

    @property
    def roles(self) -> Sequence[Role]:
        """Its roles, highest first, without @everyone."""
        ...

    @property
    def channels(self) -> Sequence[Channel]:
        """Every channel, of every kind."""
        ...

    def get_text_channel(self, channel_id: int, /) -> TextChannel | None:
        """The channel with this ID, or None when there is none, or it takes no messages."""
        ...

    def get_channel(self, channel_id: int, /) -> Channel | None:
        """The channel with this ID, of any kind, or None."""
        ...

    def get_voice_channel(self, channel_id: int, /) -> VoiceChannel | None:
        """The voice channel with this ID, or None when there is none, or it is another kind."""
        ...

    def get_role(self, role_id: int, /) -> Role | None:
        """The role with this ID, or None."""
        ...

    async def get_member(self, user_id: int, /) -> Member | None:
        """The member with this ID, from the cache or else from Discord, or None when they are not in it."""
        ...

    def can_assign(self, role: Role, /, *, by: Member | None = None, give: bool = True) -> str | None:
        """Why the bot could not give `role`, or with `give=False` take it, for `by`, or None when it could. The
        same checks as `add_role` and `remove_role` make, with nothing changed, such as for a plan."""
        ...

    async def add_role(
        self, user_id: int, role: Role, /, *, by: Member | None = None, reason: str | None = None
    ) -> None:
        """Give the member with this ID the role, in one request, whether or not teabot has seen them, with the
        checks `Member.add_role` makes. Raises `MemberGone` when they are no longer in the server."""
        ...

    async def remove_role(
        self, user_id: int, role: Role, /, *, by: Member | None = None, reason: str | None = None
    ) -> None:
        """Take the role from the member with this ID, as `add_role` gives it."""
        ...

    async def find_members(self, prefix: str, /, *, limit: int = 10) -> list[Member]:
        """Members whose name or nickname starts with `prefix`, up to `limit`, at most 100."""
        ...

    async def ban(self, user_id: int, /, *, reason: str | None = None, delete_message_days: int = 0) -> None:
        """Ban a user, whether or not they are still a member, deleting their messages from the last
        `delete_message_days` days, up to 7. Refused for the owner, or a member whose highest role is at or
        above the bot's."""
        ...

    async def unban(self, user_id: int, /, *, reason: str | None = None) -> None:
        """Lift a ban. Raises `ActionError` when the user is not banned."""
        ...

    async def bans(self, *, limit: int = 100) -> list[Ban]:
        """The server's bans, up to `limit`."""
        ...
