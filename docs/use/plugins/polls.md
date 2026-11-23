# Polls

Post Discord's own polls, which members vote in with a click, and keep their results. Discord counts the votes, shows them as they come in, ends the poll on time and posts its results. teabot adds a list of running polls, a record of each poll's final counts that outlasts its message, and an event when one ends. Turn it on with `/plugins enable plugin:polls`.

## Start a poll

```text
/polls start channel:#events question:Which event next? answers_1:Tea Maze answers_2:Battleships duration:3 days
```

teabot posts the poll and replies with its number and a link to it.

| Option | What it is |
|---|---|
| `channel` | The channel to post in. |
| `question` | What the poll asks, up to 300 characters. |
| `answers_1` to `answers_10` | The answers, 2 to 10, each up to 55 characters, none repeated. |
| `duration` | `1 hour`, `4 hours`, `8 hours`, `1 day`, `3 days`, `1 week` or `2 weeks`. `1 day` if left out. |
| `multiple` | Whether members may choose more than one answer. No if left out. |
| `ping` | A role to ping with the poll. |

A ping is refused, with the reason, when it wouldn't reach anyone: a role that can't be mentioned needs Mention Everyone in the channel, both for you and for the bot. Otherwise Discord would drop it without a word.

teabot needs View Channel, Send Messages, Send Polls and Read Message History in the channel, and says which it lacks.

Discord's polls can't be edited once posted, so to change one, end it and start another. Votes are public, and anyone who can see the channel can vote.

## End one early

```text
/polls end poll:3
```

`poll` suggests the running polls by question. teabot shows the votes so far and asks you to confirm, since ending a poll stops the voting for good. Discord then finalises it and posts its results.

## Read polls

- `/polls list` shows the running polls, with their channels and when they end.
- `/polls results poll:3` shows a poll's counts: live while it runs, and the kept final counts once it has ended.

A minute after a poll ends, teabot reads its final counts and keeps them. Discord counts votes, not voters, so in a poll where members may choose several answers, the votes add up to more than the people who voted.

If the bot can't see a poll's channel when it ends, it tries again every hour for a day, so fixing the permission in time still keeps the results. `/polls results` says when a poll's message was deleted, or its channel was lost, before its results were read.

## Events

Plugins subscribe to them, and programs read them with [`admin_events`](../ai.md#follow-a-servers-events).

| Event | When | Payload |
|---|---|---|
| `polls.ended` | A poll ended and teabot read it, or found its message deleted. | `poll`, `channel`, `message_url`, `started_by`, `question`, `answers` (each `text` and `votes`), `votes`, `multiple`, `final`, and `outcome`: `counted`, `uncounted` when Discord sent no counts, or `gone` when the message was deleted |

## Who can use it

| Command | Needs |
|---|---|
| `/polls start`, `/polls end`, `/polls list`, `/polls results` | Manage Messages |

Reading polls needs Manage Messages too, since a poll may be in a channel some members can't see. Members see a poll's counts in Discord itself. See [Who can use what](../permissions.md).

## Data

- Each poll's question, answers and final counts are kept until the server's data is removed.
- No votes are kept, or who voted. Deleting a member's data removes them as a poll's starter, and keeps the poll.

## Settings

None.

## For AI assistants

The tools are `polls_start`, `polls_end`, `polls_list` and `polls_results`.

**Next:** [Roles](roles.md).
