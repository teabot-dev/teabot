# teabot.plugins

- teabot's first-party plugins. They ship inside the `teabot` package, the way RuneLite ships its core plugins in the client. Plugins a management bot can do without, such as giveaways, are on the plugin hub instead.
- Each is registered in `pyproject.toml` under `[project.entry-points."teabot.plugins"]`, and loads exactly as a third-party plugin does.
- Each is off in a server until `/plugins enable plugin:<id>`. `TEABOT_DISABLED_PLUGINS` turns one off for a whole instance.
- Each imports only `teabot.sdk`, which an import-linter contract checks, so they stay honest examples for plugin authors.
- `announcements/`: post an announcement to a channel, now or later.
- `polls/`: Discord's own polls, started, ended early after a plan, listed, and kept with their final results, which a job reads once a poll ends and announces as `polls.ended`.
- `roles/`: a role picker, with a button for each role that members press to take it and press again to give back, and, in `changes.py`, role changes for named members or every member a filter matches, run as a paced job.
- `tickets/`: queues of items members claim, such as game accounts, and handlers who accept or reject the claims; what follows an accept belongs to subscribers of its events.
- Their tests are in `tests/plugins/<id>/`, each with its `snapshot.json`.
