# teabot-ballots

- The plugin the documentation's tutorial builds: https://teabot-dev.github.io/teabot/plugins/tutorial/
- `/ballots start` posts a ballot with two to four options; `/ballots vote` votes, and voting again changes the vote; `/ballots list` shows the running ballots; `/ballots delete` deletes one, after a plan.
- When time is up, a job closes the ballot and posts the results.
- The tutorial includes this code through the `# --8<-- [start:...]` markers. Keep them when you change it.
- It does not ship with teabot. Its tests run with teabot's own.
- External connections: none.
