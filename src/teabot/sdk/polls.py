"""Discord's own polls (polls.md): `Poll` to send one, `SentPoll` to read one, and why a poll can't be read.

Members vote with a click, and Discord counts and shows the results. A plugin sends a poll with
`channel.send(poll=...)`, reads it with `channel.read_poll(message_id)`, and ends it early with
`channel.end_poll(message_id)`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from teabot.sdk.actions import ActionError

QUESTION_MAX = 300
ANSWER_MAX = 55
ANSWERS_MIN = 2
ANSWERS_MAX = 10
SHORTEST = timedelta(hours=1)
LONGEST = timedelta(days=32)


@dataclass(frozen=True)
class Poll:
    """A poll to send.

    - `question` is up to 300 characters.
    - `answers` are 2 to 10, each up to 55 characters, none blank or repeated. Discord takes one, but a poll of one
      asks nothing, so teabot asks for two.
    - `duration` is 1 hour to 32 days, in whole hours: Discord ends the poll then.
    - `multiple` lets a member choose more than one answer.

    A poll that breaks a limit raises `ValueError` naming it.
    """

    question: str
    answers: tuple[str, ...]
    duration: timedelta = timedelta(days=1)
    multiple: bool = False

    def __post_init__(self) -> None:
        question = self.question.strip()
        if not question or len(question) > QUESTION_MAX:
            raise ValueError(f"A poll's question is 1 to {QUESTION_MAX} characters.")
        answers = tuple(a.strip() for a in self.answers)
        if not ANSWERS_MIN <= len(answers) <= ANSWERS_MAX:
            raise ValueError(f"A poll has {ANSWERS_MIN} to {ANSWERS_MAX} answers.")
        if any(not a or len(a) > ANSWER_MAX for a in answers):
            raise ValueError(f"Each answer is 1 to {ANSWER_MAX} characters.")
        if len({a.lower() for a in answers}) != len(answers):
            raise ValueError("Each answer appears once.")
        if not SHORTEST <= self.duration <= LONGEST or self.duration % timedelta(hours=1):
            raise ValueError("A poll lasts 1 hour to 32 days, in whole hours.")
        object.__setattr__(self, "question", question)
        object.__setattr__(self, "answers", answers)


@dataclass(frozen=True)
class PollAnswer:
    """One answer of a sent poll: Discord's `id` for it, its `text`, and its `votes`, zero for one with none."""

    id: int
    text: str
    votes: int


@dataclass(frozen=True)
class SentPoll:
    """A poll as Discord shows it now.

    - `question`, `multiple` and `ends_at`, when Discord ends it, or ended it.
    - `answers`, each with its votes, and `votes`, the sum of them, which equals the people who voted only in a
      single-choice poll: Discord keeps no count of voters.
    - `final`, whether Discord has finalised the counts, a little after the poll ends. Until then they are live and
      may be incomplete, and Discord may not have sent any, which reads the same as no votes. `final` is the one
      signal to trust them.
    """

    question: str
    multiple: bool
    ends_at: datetime | None
    answers: tuple[PollAnswer, ...]
    final: bool

    @property
    def votes(self) -> int:
        """Every answer's votes together: the people who voted only in a single-choice poll."""
        return sum(a.votes for a in self.answers)


class MessageHidden(ActionError):
    """The bot can no longer see the message's channel, or read its history: a permission to fix, never a sign
    the message is gone."""


class MessageGone(ActionError):
    """The message has been deleted."""
