# Your first plugin

A teabot plugin is a small Python package: functions that become slash commands and AI assistant tools, a database table or two of its own, and tests. At the end of this page, you have made one, tested it, and run it in Discord. It takes about ten minutes.

!!! note "Before you start"

    You need:

    - A teabot instance from the [quick start](../host/index.md), with its folder open in a terminal.
    - [uv](https://docs.astral.sh/uv/getting-started/installation/) on your machine, so your editor and the tests can use the plugin's code:

        ```bash
        curl -LsSf https://astral.sh/uv/install.sh | sh
        ```

    - Python you can read. A little SQL helps.

## 1. Make the plugin

In your instance's folder:

```bash
uv run teabot new-plugin ballots
```

```text
created plugins/ballots
next: uv run --package teabot-ballots pytest plugins/ballots
then try it in Discord: docker compose stop bot, then uv run teabot dev
and in your server, /plugins enable plugin:ballots
```

It writes one package, with an example action called `hello`:

```text
plugins/ballots/
    pyproject.toml                      the package, and how teabot finds it
    README.md                           what it does, and every server it contacts
    src/teabot_ballots/
        __init__.py                     the plugin
        migrations/0001_greetings.sql   its table
        py.typed                        tells type checkers the package has types
    tests/
        test_ballots.py                 its tests
        test_ballots_snapshot.py        a record of its commands, kept in snapshot.json
```

## 2. Run its tests

```bash
uv run --package teabot-ballots pytest plugins/ballots
```

```text
3 passed in 1.02s
```

The tests run against your instance's Postgres, which `setup.sh` started. Each test gets a database of its own, and drops it afterwards. The first run also writes `tests/snapshot.json`.

## 3. Read the plugin

This is all of `src/teabot_ballots/__init__.py`:

```python title="plugins/ballots/src/teabot_ballots/__init__.py"
--8<-- "tests/docs/scaffold/ballots/src/teabot_ballots/__init__.py"
```

- `plugin = Plugin("ballots")` is the plugin. Its id, `ballots`, is the command's name: `/ballots`.
- `@plugin.action()` makes `hello` an action: the slash command `/ballots hello`, and the AI assistant tool `ballots_hello`.
- Its parameters after `ctx` are the command's options. The docstring's first line describes the command, and its `Args:` section describes each option.
- `ctx` is the call: which server (`ctx.guild`), who asked (`ctx.actor`), and the plugin's database (`ctx.db`).
- It returns a `Greeted`, and `render` is the reply the member sees.

The tests call the action the way Discord would, permissions and all:

```python title="plugins/ballots/tests/test_ballots.py"
--8<-- "tests/docs/scaffold/ballots/tests/test_ballots.py"
```

## 4. Change it

Make the reply your own. In `__init__.py`, change `render`:

```python
    def render(self) -> str:
        return f"Hi there! That is hello number {self.times}."
```

Run the tests again. They still pass, since they check what the action returns, not how it is shown.

## 5. See it in Discord

Run the bot from your plugin's code, so each save restarts it with your changes. Stop the Compose bot first, since only one process can run the bot:

```bash
docker compose stop bot
uv run teabot dev
```

`teabot dev` watches `plugins/` and `teabot.env`, and restarts the bot when a file changes. Each restart migrates the plugin and sends its commands to the servers where it is on. Stop it with ++ctrl+c++. Then turn the plugin on in your server:

```text
/plugins enable plugin:ballots
/ballots hello
```

teabot replies `Hi there! That is hello number 1.`

## Next

- The [tutorial](tutorial.md) grows `ballots` into a real plugin: options, votes, a job that posts the results, and a confirmed delete.
- The [reference](reference.md) has every part of a plugin in one page.
