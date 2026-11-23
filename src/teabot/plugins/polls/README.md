# polls

- A first-party plugin: Discord's own polls, started, ended early after a plan, listed, and kept with their final results, which a job reads once a poll ends and announces as `polls.ended`.
- What it does, its commands and who can use them: https://teabot-dev.github.io/teabot/use/plugins/polls/
- `__init__.py` holds the actions, and `reading.py` the job that reads a poll's final results.
- External connections: none.
- Tests: `tests/plugins/polls/`, through `teabot.testing`.
