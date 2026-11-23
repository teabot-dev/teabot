# Polls

Polls built on Discord's own polls: members vote with a click, and Discord counts and shows the results. teabot gets the SDK pieces any plugin needs to post and read a poll, and a first-party polls plugin uses them to start polls, end them, list them and keep their results.

## Why

- **Discord's polls are the better tool for voting.** Members vote in place, change their vote, and see live results, with nothing to type. A poll made of slash commands, as the tutorial's is, can't match that.
- **The tutorial stays a tutorial.** It teaches actions, jobs and tables with four fixed options, and isn't widened into a real plugin.
- **What Discord doesn't do, teabot adds:** a record of each poll's final results kept after the message is gone, a list of running polls, results readable over MCP and the service API, and an event other plugins and programs react to.

## What Discord's polls are

- A question of up to 300 characters, and up to 10 answers of up to 55 characters each.
- One choice or several.
- A duration of 1 hour to 32 days, in whole hours, 24 by default. Discord's own app offers 1 hour, 4 hours, 8 hours, 1 day, 3 days, 1 week and 2 weeks. Discord ends the poll then, or earlier when its sender ends it.
- Once posted, a poll can't be edited. Only its sender can end it early.
- Votes are visible: anyone can see who voted for what. A poll can't be anonymous or limited to a role; anyone who can see the channel votes.
- When a poll ends, Discord finalises its counts, a little after, and posts a results message in the channel.
- Its results hold, per answer, a count of votes. There is no count of voters, so in a multiple-choice poll the votes add up to more than the people who voted. An answer with no votes may be missing from the counts, and a message whose results Discord hasn't computed has none at all, which means unknown, not zero.
- Sending one needs the Send Polls permission in the channel, which a channel can deny while allowing Send Messages.
- discord.py supports all of it from 2.4, and teabot requires 2.6, and teabot's default intents already include polls, so vote events reach the bot.

## Part 1: polls in the SDK

### Sending one

```python
poll = Poll(question="Which event next?", answers=["Tea Maze", "Battleships"], duration=timedelta(days=2))
message = await channel.send(poll=poll, ping_roles=[events_role.id])
```

- **`Poll`** is an SDK type: a question, 2 to 10 answers as text, a duration, and `multiple`. Two answers at least is teabot's rule, since a poll of one asks nothing. It is checked when made against Discord's limits, and refuses blank or repeated answers, which would confuse voters. A wrong one raises `ValueError` saying which.
- **`channel.send(poll=...)`** sends it at once and returns the `Message`, whose `id`, `jump_url` and `poll_ends_at` the plugin keeps. `Message` gains `poll_ends_at`, when Discord ends the poll, or None for a message with no poll. `content` may go with it. teabot offers no buttons or select menu with a poll.
- **Before sending,** it checks the bot's permissions in the channel: Send Messages, or Send Messages in Threads in a thread, Send Polls, and View Channel and Read Message History, without which the poll couldn't be read afterwards. A missing one raises `ActionError` naming it, since Discord's own refusal doesn't.
- The outbox, `tx.send`, takes no poll: a queued post has no Discord message ID until it goes out, and every use of a poll needs one.

### Pinging roles

- **`channel.send` takes `ping_roles`,** role IDs a message pings, beside `ping` for member IDs. `send` writes each role's mention into the message's content, before any content given, and allows only those, so nobody else is pinged. The outbox's `tx.send` doesn't take it yet: nothing in this design needs it, and its stored format would change.
- **`channel.can_mention(role, by=member)`,** on the channel since Mention Everyone is a channel permission, says why a role ping wouldn't work there, or None when it would. The member may ask for it when the role is mentionable or they have Mention Everyone in the channel. The bot can deliver it when the role is mentionable or the bot has Mention Everyone there; otherwise Discord drops the ping without a word. Announcements can use both later.

### Reading results

**`await channel.read_poll(message_id)`** reads a message's poll, as a `SentPoll`:
- the `question`, `multiple` and `ends_at`;
- each answer's `id`, `text` and `votes`, zero for an answer with none;
- `votes`, the sum of every answer's votes, which equals the people who voted only in a single-choice poll;
- `final`, whether Discord has finalised the counts. Until it has, the counts are live and may be incomplete, and Discord may not have sent them at all, which reads the same as no votes; `final` is the one signal to trust them.

It returns None when the message is deleted or has no poll, and raises **`MessageHidden`**, an `ActionError`, when the bot can no longer see the channel or read its history, so a plugin never mistakes a permission problem for a deleted poll. A deleted channel is gone too: `get_text_channel` returns None. `channel.get_message` is unchanged, so the roles plugin, which treats both cases alike, keeps working.

Voters by answer aren't read: counts are what results need, and voters are member data a poll's record would then have to keep and delete.

### Ending early

**`await channel.end_poll(message_id)`** ends a poll the bot sent, by its message's ID, with no fetch first. Discord then finalises it, as when time runs out. Ending a poll that has ended already does nothing, since the SDK catches Discord's refusal. Ending one whose message was deleted raises `MessageGone`, an `ActionError`.

### Testing

- `FakeChannel.send(poll=...)` records the poll on the sent message.
- **`harness.vote(message, member, answer)`** changes a fake poll's counts, with `answer` its text. In a single-choice poll, a member's second vote moves their first, as in Discord.
- **`harness.end_poll(message, final=True)`** ends it, finalised unless `final=False`.

### Not in this part

- **Vote events.** The bot already receives them. An `on_poll_vote` event, with the member, the message, the answer and whether the vote was added or removed, is added when a feature needs live votes, such as "who hasn't voted yet".
- **Voters** by answer, for the same reason.

## Part 2: the polls plugin

A first-party plugin, `polls`, shipped inside teabot like roles and tickets, off in a server until it is enabled.

### Its name

This design is D32's native polls, made definitive. The tutorial's plugin is called `polls` too. teabot loads neither of two plugins that declare one ID, and the tutorial's migration, once applied under `polls`, would block the first-party plugin's.

- **The tutorial plugin becomes `ballots`,** one word, so its commands read naturally, such as `/ballots start`, and everything that names it moves with it:
  - `examples/polls`, and its entry point;
  - `docs/plugins/index.md`, `docs/plugins/tutorial.md`, `docs/plugins/reference.md`'s diagram, and the nav's "Tutorial: a polls plugin" in `zensical.toml`;
  - `tests/docs/scaffold/polls`, `tests/docs/test_docs.py`, which renders `new-plugin polls`, and `tests/docs/README.md`;
  - in `pyproject.toml`: the `teabot-polls` dev dependency, its `[tool.uv.sources]` entry, the first-party packages, and the import-linter root packages and contract; then `uv.lock`, relocked;
  - `design/architecture.md`.
  
  It is renamed before the polls plugin lands, and the tutorial links to the polls plugin, so readers don't take the tutorial's for the real feature. Old changelog entries keep the name they had.
- **`teabot new-plugin` already refuses the ID of any plugin shipped inside teabot,** from teabot's own entry points, so once `polls` ships, a new plugin never takes it.
- **An instance running a plugin of its own called `polls`,** as anyone who followed the tutorial is, loses both at upgrade. The release notes' operator section gives the manual step: rename that plugin's ID, run `teabot plugins purge polls`, or move its data first, then enable the first-party plugin. teabot's start-up log names the clash and the fix, so it never fails without a word.

### What it keeps

`polls`, one row per poll:
- its `id`, the channel, and the poll's message ID;
- the question, the answers, and whether it is multiple choice;
- `ends_at`, taken from the message Discord returned;
- `ended_at`, set only when it is ended early; otherwise it ends at `ends_at`;
- its `state`: `running`, `ended`, `gone` for a poll whose message was deleted before its results were read, or `unread` for one the bot could no longer see;
- `results`: the final votes per answer and in all, whether they were final, once read;
- `started_by`, a `teabot.member_actor` column, so deleting a member's data keeps the poll. Who ended one early is in the audit log.

No member's vote is stored, only counts.

### Actions

- **`polls.start(channel, question, answers, duration?, multiple?, ping?)`:** a write; posts the poll and records it.
  - `answers` is a list of 2 to 10, as slash options `answers_1` to `answers_10`.
  - `duration` is one of Discord's own: 1 hour, 4 hours, 8 hours, 1 day, 3 days, 1 week or 2 weeks, a day if left out.
  - `ping` is an optional role pinged with the poll, refused with `channel.can_mention`'s reason when it wouldn't work.
  - It needs Manage Messages, as announcements do, and replies with the poll's link.
  - It posts first, then records the poll and queues its job in one transaction. If that transaction fails, it deletes the message it posted. A crash between the two can still leave a poll with no record, which does little harm: Discord ends it and posts its results as usual.
- **`polls.end(poll)`:** destructive, since ending early can't be undone and cuts voting off. Its plan shows the poll's question and current counts, and confirming ends it and records `ended_at`. For members with Manage Messages. The `poll` option suggests running polls by question.
- **`polls.list`:** a read, from teabot's record alone, with no request to Discord; the server's running polls, with their questions, channels and when they end. A poll counts as running while it hasn't passed `ended_at`, or `ends_at`, whatever its state, so an ended poll leaves the list at once, before its job runs.
- **`polls.results(poll)`:** a read; a poll's results: live counts while it runs, read from the message, and the kept final counts once it has ended.

Every action works on every door, slash commands, MCP and the service API, up to a token's risk. `polls.list` and `polls.results` need Manage Messages, as start and end do, for simplicity: a poll may sit in a channel some members can't see, and the SDK doesn't offer a member's view of a channel. Members see results in Discord anyway. Only the job and `end` change a poll's record; `results` finds a deleted message and says so, without writing it. Questions and answers in their output are `UntrustedText`.

### Reading the final results

- **`start` schedules a job, `read_results(poll, tries)`, keyed `poll:<id>`, for one minute after `ends_at`;** `end` moves it, by that key, to a minute after the early end. The minute gives Discord time to finalise. The key stays the same as `tries` changes.
- The job reads the poll with `channel.read_poll`, and decides on `final` alone:
  - **final:** it stores the counts, marks the poll ended, and emits `polls.ended`;
  - **not final:** it runs again a minute later, up to ten times, then stores what it read, marked not final, and emits. When every count is still zero, which may mean Discord never sent them, it stores no results, and the event says so, rather than announcing zeros;
  - **the message is gone:** it marks the poll `gone`, with no results, and emits `polls.ended` flagged gone, so a subscriber waiting on the poll hears its end;
  - **the bot can't see the channel:** it runs again an hour later, up to 24 times, so a permission fixed within a day still gets the results. After that it marks the poll `unread`, which leaves `polls.list`, and `polls.results` says the bot lost sight of the channel.
- A restart or deploy loses nothing, since the job is queued.

### Events

`polls.ended`, about one poll, with:
- the poll's ID, channel and message link, and who started it, as an optional `MemberRef`, empty once their data is deleted;
- the question and each answer's text, as `UntrustedText`, and each answer's votes;
- the votes in all, `multiple`, whether the counts are final, and whether the poll was gone, or had no counts, in which case there are none.

A plugin or program reacts to it, such as one that turns a winning answer into an event post.

### What it does not do

- It posts no summary of its own: Discord posts the results message.
- It doesn't restrict who votes, and polls aren't anonymous, since Discord's polls can't be.
- It doesn't edit a poll after posting; to change one, end it and start another.

### Data and privacy

- Every poll's row, with its results, is kept until the server's data is removed (D49), since a lasting record is what the plugin adds. The rows are small and hold no member's votes.
- No votes or voters are kept, so deleting a member's data only empties `started_by`. The question and answers a member wrote stay with the poll, as the server's content, as a message they posted would.

## Order of work

1. The SDK: `Poll`, sending through `channel.send`, `ping_roles` and `channel.can_mention`, `channel.read_poll` and `SentPoll`, `channel.end_poll`, `MessageHidden` and `MessageGone`, `Message.poll_ends_at`, and the testing fakes.
2. The tutorial plugin renamed `ballots`, and the start-up message for a plugin ID clash.
3. The polls plugin, its docs page, its snapshot, its event, and the release notes' operator step.
