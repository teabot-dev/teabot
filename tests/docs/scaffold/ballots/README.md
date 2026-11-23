# teabot-ballots

- `/ballots hello`: says hello, and counts how often the member has. A starting point to replace with your own actions.
- Its table is `greetings`, created by `migrations/0001_greetings.sql`. Add a new numbered file for each change.
- Tests need the Postgres of the instance's Compose file: `docker compose up -d --wait postgres`.
- Then run them with `uv run --package teabot-ballots pytest plugins/ballots`, on a development machine, since they create and drop databases.
- `tests/snapshot.json` records the plugin's commands, tools and job payloads. Accept a change to them by adding `--teabot-accept` to the test command.
- `uv run teabot check plugins/ballots` runs the review checklist; the tests run it too.
- Turn it on in a server with `/plugins enable plugin:ballots`.
- Writing actions, jobs, config and plans: the plugin reference, https://teabot-dev.github.io/teabot/plugins/reference/
- External connections: none. List every host the plugin contacts here.
