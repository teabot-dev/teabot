# Tutorial: a ballots plugin

This grows the `ballots` plugin from [Your first plugin](index.md) into a real one. At the end, members can start ballots, vote and change their votes, a job posts the results when time is up, and moderators can delete a ballot after confirming a plan. Every piece is tested.

The finished plugin is [`examples/ballots`](https://github.com/teabot-dev/teabot/tree/main/examples/ballots) in teabot's repository. Its tests run with teabot's own, so the code on this page works as shown.

## 1. The plugin and its settings

Start `src/teabot_ballots/__init__.py` afresh. Its imports, for the whole tutorial, are:

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:imports"
```

Below them, the plugin and its settings:

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:plugin"
```

- `Cfg` is the plugin's settings. Server admins change them with `/plugins config plugin:ballots setting:default_minutes value:90`. Give every setting a default, so a server that has set nothing still works.
- `RoleRef` holds a role's ID.
- `type Ctx = Context[Cfg]` names the settings' type once. Then `ctx.config` is a `Cfg` in every action, and pyright checks it.

Delete `tests/test_ballots.py`, which tests the old `hello`. Step 8 writes the new tests, so add each step's code to the file as you go, and run the tests at step 8.

## 2. The tables

Delete `src/teabot_ballots/migrations/0001_greetings.sql`, and write `src/teabot_ballots/migrations/0001_ballots.sql`:

```sql title="src/teabot_ballots/migrations/0001_ballots.sql"
--8<-- "examples/ballots/src/teabot_ballots/migrations/0001_ballots.sql"
```

- Every table has a `guild_id`, the server's ID. teabot shows plugin code only the current server's rows, which keeps each server's ballots apart. The queries filter by `guild_id` too, which keeps them clear and lets Postgres use the index.
- A foreign key includes `guild_id` too, so a vote can never point at another server's ballot.
- teabot checks these rules when it loads the plugin, and stops with the fix if a table breaks one.
- Migrations only go forward. Once a plugin is released, add a `0002_...sql` for a change instead of editing this file.

The queries this tutorial uses sit together near the top of the file:

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:queries"
```

## 3. Starting a ballot

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:start"
```

- `channel: TextChannel` gives the member a channel picker, and the function the channel itself.
- `Annotated[int, Field(ge=1, le=10080)]` sets limits that Discord enforces before the member sends.
- `option_3: Choice | None = None` is an option the member may leave out.
- `needs="manage_messages"` is the Discord permission the action needs. teabot checks it every time.
- `ctx.now` is the time. Read the clock only through it, since tests move it.
- The closing job is scheduled in the same transaction as the ballot, so neither exists without the other. The post is sent after the transaction, since nothing that waits on Discord belongs inside one.

## 4. Voting

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:vote"
```

- A plain string is a reply.
- `ActionError` tells the member what went wrong, in your words. teabot records it in the audit log.
- `ctx.actor.user_id` is who asked. `ctx.guild.get_member(...)` finds them, with their roles.
- The vote query does the checks in one statement: the ballot is running, the option exists, and a second vote replaces the first. When nothing fits, Postgres reports `INSERT 0 0`.

## 5. Listing the ballots

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:list"
```

- `risk=READ` says the action changes nothing.
- `reply="public"` shows the reply to the channel. Replies are private by default.
- `ctx.db.fetch(Running, ...)` returns a list of `Running`, each row checked against the model. A list of models is shown one line per item.
- `UntrustedText` marks text a member wrote, so AI assistants receive it as data and never as instructions.
- The trailing underscore keeps Python's `list` free. The command is still `/ballots list`.

## 6. Closing a ballot: a job

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:close"
```

- A **job** runs later, on teabot's queue. Its parameters after `ctx` are what it is scheduled with: `close.bind(ballot=ballot)` in step 3, which pyright checks.
- A job's key is its name and arguments, so scheduling the same `close` again moves it, and `tx.unschedule(close.bind(ballot=...))` removes it.
- A job can run twice, after a crash. This one locks the ballot and checks it is still running first, so a second run changes nothing.
- `tx.send` posts the results with the transaction: if the ballot is closed, the results go out, and if anything fails first, neither happens. teabot posts them a moment after the commit, and retries while Discord is down.
- Messages mention nobody unless you say so with `ping=`. An `@everyone` in a question stays plain text.

## 7. Deleting a ballot: plan, then apply

```python
--8<-- "examples/ballots/src/teabot_ballots/__init__.py:delete"
```

- `risk=DESTRUCTIVE` is for changes that are hard to undo. Such an action needs a plan, written with `@delete.plan`.
- The plan returns a `Diff` of what would change. The member sees it, and [confirms it](../use/confirming.md) within 15 minutes. If anything it shows changed meanwhile, they get a fresh plan instead.
- The action itself then runs as a job. The member does not see its reply; they see that the change is applying.

## 8. Tests

Write `tests/test_ballots.py`, with tests for each part. The finished file is [`examples/ballots/tests/test_ballots.py`](https://github.com/teabot-dev/teabot/blob/main/examples/ballots/tests/test_ballots.py) in teabot's repository; here are the parts that show something new.

Tests import from `teabot.testing`, and from your plugin:

```python
--8<-- "examples/ballots/tests/test_ballots.py:imports"
```

A helper starts a ballot:

```python
--8<-- "examples/ballots/tests/test_ballots.py:helper"
```

The results, after the clock moves past the end:

```python
--8<-- "examples/ballots/tests/test_ballots.py:results"
```

- `harness.call(start, ...)` runs the action through teabot's real path, permissions and audit included. Pyright checks the arguments against the function, and `out` is a `Started`.
- It calls as the server's owner, its first member, who has Administrator. `harness.as_member(member).call(...)` calls as someone else.
- `harness.advance(minutes=61)` moves the clock: `ctx.now`, `now()` in your SQL, and when jobs fall due. `harness.run_jobs()` then runs the due jobs, and raises with the job's own error if one fails.

Settings, as an admin would set them:

```python
--8<-- "examples/ballots/tests/test_ballots.py:role"
```

A plan, confirmed:

```python
--8<-- "examples/ballots/tests/test_ballots.py:delete"
```

And a job run a second time, which must change nothing:

```python
--8<-- "examples/ballots/tests/test_ballots.py:twice"
```

Run them:

```bash
uv run --package teabot-ballots pytest plugins/ballots
```

The snapshot test fails once, because the commands changed from `hello` to the ballot commands. That is its job: it stops a renamed option or job argument reaching your servers by accident. Accept the new commands:

```bash
uv run --package teabot-ballots pytest plugins/ballots --teabot-accept
```

The tests also run teabot's checks on your code. They refuse imports from teabot's internals, reading the clock directly, `SET` in SQL, and a README without its "External connections".

## 9. Growing past one file

Move `plugin = Plugin(...)` and `type Ctx` into `plugin.py`, and import them from every other module. teabot imports every module of the package when it loads the plugin, so actions in `actions.py` and jobs in `jobs.py` register themselves. It skips `tests`, modules whose names start with `_`, and `__main__`.

## 10. Ship it

1. Update the plugin's `README.md` and the docstring at the top of `__init__.py`, which still describe `hello`, to list the ballot commands.
2. Stop `teabot dev` with ++ctrl+c++, and rebuild the Compose bot with the plugin in it, so it runs without you:

    ```bash
    docker compose up -d --build
    ```

    Then turn the plugin on in your server, if it is not on already:

    ```text
    /plugins enable plugin:ballots
    ```

To share it with other instances, see [Publishing a plugin](publishing.md).
