# tests/docs

- Tests that keep the documentation site true to the code.
- `scaffold/ballots/` is `teabot new-plugin ballots` rendered and committed, for the "Your first plugin" page to include. `test_docs.py` fails if it differs from a fresh render.
- `test_docs.py` also checks that every built-in command is in the commands table, that every first-party plugin has a page naming each of its actions, and that `teabot.env.example` names every setting.
