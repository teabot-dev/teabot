# Changelog

Every release of `teabot` and the instance template, newest first. Up to 1.0.2, the announcements plugin was released beside them as `teabot-announcements`. The versions follow semantic versioning, which `teabot.sdk` keeps from 1.0. A **For operators** section lists any manual step an upgrade needs.

<!-- --8<-- [start:releases] -->

## Unreleased

### Writing plugins

- Discord's own polls: `channel.send(poll=Poll(...))` posts one, `channel.read_poll` reads its counts as a `SentPoll`, and `channel.end_poll` ends it early. `MessageHidden` tells a channel the bot can no longer see apart from a deleted message. `harness.vote` and `harness.end_poll` test them. See [Polls](https://teabot-dev.github.io/teabot/plugins/reference/#polls).
- `channel.send(..., ping_roles=[...])` pings roles, and `channel.can_mention(role, by=member)` says whether such a ping would reach them.
- The tutorial's plugin is called `ballots`, since teabot ships a `polls` plugin, so the tutorial starts with `teabot new-plugin ballots`.
- When two plugins declare one ID, the start-up log says how to fix it, as well as which packages declare it.

### For operators

- teabot ships a plugin called `polls`. A plugin of your own with that ID, such as one built from the tutorial before it was renamed, stops both from loading. Before upgrading, give yours another ID, or uninstall it. Data your plugin kept under `polls` stays with the ID, so run `teabot plugins purge polls` before enabling teabot's.

## 1.15.0 - 2026-11-23

### Using teabot

- A ticket's Claim form fits Discord's 45 characters, and a claim on an account that is already linked, or has left the clan, says why it can't be claimed.

### Writing plugins

- An `@action.autocomplete` function reads the options the member filled in before the one it suggests for as `ctx.filled`, such as the poll whose options to suggest. `harness.autocomplete(..., filled={...})` tests it. See [Suggestions](https://teabot-dev.github.io/teabot/plugins/reference/#suggestions).

## 1.14.0 - 2026-11-20

### Using teabot

- The tickets plugin: queues of items members claim, such as game accounts, with a board to claim from, and handlers who accept, reject or revoke claims, and grant tickets to members who didn't claim them. What follows an accept belongs to whoever subscribes to its events. See [Tickets](https://teabot-dev.github.io/teabot/use/plugins/tickets/).
- `admin_events` pages a server's plugin events by cursor, so a program on the service API, or an assistant, follows what happens there. See [Follow a server's events](https://teabot-dev.github.io/teabot/use/ai/#follow-a-servers-events).

### Writing plugins

- Plugin events: a plugin declares what it emits with `Plugin(emits=[Event(...)])` and emits inside its transaction with `tx.emit`, and other plugins subscribe with `@plugin.on_event("<plugin>.<event>", Model)`, delivered at least once as jobs. See [Plugin events](https://teabot-dev.github.io/teabot/plugins/reference/#plugin-events).
- `teabot.member_actor`, a column type for a member who only took part in a row: deleting their data empties it and keeps the row.

## 1.13.0 - 2026-11-20

### Using teabot

- The audit channel shows each change as one line, with members, roles and channels as mentions that notify nobody, and gathers a run of changes by one member for one action into one post. See [The audit log](https://teabot-dev.github.io/teabot/use/audit/).
- Role rules keep a set of roles following a plugin's labels, such as a clan's ranks: within a scope, each member holds the role of the first label they have. A run above its cap waits for an admin. See [Roles](https://teabot-dev.github.io/teabot/use/plugins/roles/#keep-roles-following-labels).

### Writing plugins

- `ctx.log` adds a line under a handed-off apply to the audit log as often as needed, for work that goes on under one grant.
- An action no slash command offers may take a list of up to 25.

## 1.12.0 - 2026-11-18

### Using teabot

- `admin_members` filters on labels a plugin keeps about members, such as which members a clan plugin has linked to their game accounts.

### Writing plugins

- Member labels: a plugin that declares `Plugin(..., writes=["labels"])` keeps short strings about members with `ctx.labels.replace_all`, which `MemberFilter(labels_from=..., labels_any=[...])` matches while they are fresh. `@plugin.on_labels_changed` hears them change. See [Member labels](https://teabot-dev.github.io/teabot/plugins/reference/#member-labels).
- `ctx.members.count` and `find` take several filters, which a member must all match.
- `action.asking(...)` makes a button that leaves text inputs out and asks for them in a form when pressed.

## 1.11.0 - 2026-11-18

### Using teabot

- Give a role to members, or take it away. `/roles give` and `/roles take` change up to ten named members, after a plan. AI assistants, the service API and the site also have `roles_give_matching` and `roles_take_matching`, which change every member a filter over the member directory matches, up to 10,000, such as "take Friends from everyone who has Clan Member". They run a few members a second, which Discord's limits allow, and resume after a restart. `roles_list_changes` follows them, `roles_stop` stops one and `roles_revert` undoes one. See [Roles](https://teabot-dev.github.io/teabot/use/plugins/roles/).
- teabot gives or takes a role for you only within what you could do yourself: below your highest role and, to give it, with every permission it grants. Through an AI assistant it never gives or takes a role with a moderator's permission.
- A role picker can no longer offer a role that grants a permission its admin lacks, such as Create Events.
- A plan can say more than its changes, such as how many members a change holds and how long it will take.

### Writing plugins

- `ctx.members` reads the member directory, for a plugin that declares `Plugin(..., reads=["members"])`: `count` and `find` present members by a `MemberFilter`, the filter `admin_members` takes. See [The member directory](https://teabot-dev.github.io/teabot/plugins/reference/#the-member-directory).
- An action can take a model as an input, such as `filter: MemberFilter`, whose fields become inputs of their own.
- `Member.add_role` and `remove_role` take `by=`, the member a change is made for, and refuse what that member could not do themselves. `Guild.add_role(user_id, role, by=)` changes a member by ID without fetching them, raising `MemberGone` when they have left. `Guild.can_assign` says whether a change would be refused. A plugin's `reason=` for a role change now follows teabot's own part of Discord's audit log text rather than replacing it.
- `Diff(notes=[...])` shows lines above a plan's entries, never hashed.
- An apply can return `HandedOff` to hand long work to a job, which checks it may go on with `ctx.authorized(ctx.audit_id)` and closes it with `ctx.record_outcome`. See [Work that outlasts its apply](https://teabot-dev.github.io/teabot/plugins/reference/#work-that-outlasts-its-apply).
- `DANGEROUS`, the permissions that act on others or the server, is part of the SDK.

## 1.10.0 - 2026-11-17

### Using teabot

- teabot keeps a directory of each server's members, who has left, how each joined, and their history: joins, leaves, kicks, bans, timeouts, role and nickname changes, with who made them. AI assistants and the service API read it with `admin_members`, which filters and counts members like a table, and `admin_member_history`. Both need Manage Server. See [Members and permissions](https://teabot-dev.github.io/teabot/use/members/).
- `admin_channels` lists every channel's permission overwrites, and `admin_view_as` shows the server as a set of roles sees it, as Discord's **View server as role** does. Both need Manage Server.
- `/admin find_members` finds members by username and display name as well as nickname, once the directory has its first copy of the server.

### For operators

- On its first start, teabot copies every server's member list once, a thousand members a request, which takes a few seconds per thousand members.
- Your privacy policy should say what the member directory keeps and for how long. See [Your teabot's site](https://teabot-dev.github.io/teabot/host/production/#your-teabots-site).

### Self-hosting

- `TEABOT_DIRECTORY_RETENTION_DAYS` is how long the directory keeps its history and members who have left, a year if left out. `TEABOT_DIRECTORY_MEMBER_LIMIT` turns it off in servers with more members. `TEABOT_DIRECTORY_JOIN_LOOKUP=false` leaves join method unknown, rather than read it from Discord's member search, which Discord's published API docs do not list.
- `teabot users delete` deletes the member's rows and history in the directory too.

### Writing plugins

- `on_member_roles` hears every member's role changes, not only those of members teabot has cached, so a plugin with it in a busy server runs more jobs.
- `@plugin.on_member_join(late=True)`, and the same for `on_member_leave` and `on_member_roles`, also hears changes teabot found after a gap, such as a restart, with when the gap began. See [Changes found late](https://teabot-dev.github.io/teabot/plugins/reference/#changes-found-late).

## 1.9.0 - 2026-11-16

### Self-hosting

- Voice needs teabot's `voice` extra, `teabot[voice]`, which pins davey below 0.2: with discord.py 2.7, davey 0.2 never switches to the call's encryption keys, so listeners hear silence while the bot shows as speaking. A plugin that plays audio depends on the extra itself.
- When the bot is moved or disconnected from a voice channel by someone else, its session follows or ends.

### Writing plugins

- `ctx.http` works in event handlers, such as `on_message`, which reached no host before.
- A plugin that streams media, such as music, may reach its media hosts through its media library, such as yt-dlp, when its README lists them. See [Reaching the web](https://teabot-dev.github.io/teabot/plugins/reference/#reaching-the-web).
- Voice: `ctx.voice` joins a voice channel, plays Opus packets the plugin supplies, pauses, stops and leaves. The plugin that joins holds the server's one connection. `@plugin.on_track_end` runs when audio runs out or fails, `@plugin.on_voice_resume` after a restart, so a plugin carries on where it was, and `@plugin.on_voice_alone` when everyone else has left the bot's channel. A `VoiceChannel` input is a picker of voice channels. The harness plays through a fake connection, with `harness.voice_connection`, `harness.end_track()`, `harness.restart_voice()`, and `harness.voice(member, left=channel)` runs `on_voice_alone`.

## 1.8.0 - 2026-11-16

### Using teabot

- A teabot with a public address has its own site there. Its home page shows the bot and an **Add to your server** button. Its plugin browser, at `/plugins`, shows each plugin, and once you sign in with Discord and choose a server, you turn plugins on and off and change their settings in place. It replaces the dashboard, and `/dashboard` leads there. See [Your teabot's site](https://teabot-dev.github.io/teabot/use/site/).

### For operators

- Signing in now returns to `https://your.hostname/auth/callback`. Before upgrading, add it under **OAuth2**, **Redirects** in the Discord developer portal. Admins cannot sign in until it is there.
- Caddy must forward the whole site. Replace `/etc/caddy/teabot.caddy` with the template's `deploy/bare-metal/teabot.caddy`, with your hostname in it, and reload Caddy.
- Put your privacy policy and terms in `pages/privacy.md` and `pages/terms.md`, and the site shows them at `/privacy` and `/terms`. A Docker instance needs a `pages/` folder, even an empty one, since its image copies it.

### Self-hosting

- `TEABOT_SITE_PAGES` names the folder with `privacy.md` and `terms.md`, `pages` if left out.
- `/plugins.json` lists the plugins your teabot runs, which the plugin hub reads.

### Writing plugins

- `Plugin(..., description="...")` says in a sentence what a plugin does, for a teabot's plugin browser.

## 1.7.0 - 2026-11-16

### Writing plugins

- A plugin reads anyone's message with `TextChannel.read_message(id)`, and finds who reacted with an emoji, bots left out, with `TextChannel.reactors(message_id, emoji)`.
- `ReceivedMessage` carries the message's `attachments`, each with its `filename`, `url`, `content_type` and whether it is an `image`, and says when it was posted, `created_at`. `on_message` and message menus fill them in too.
- A `Channel` says whether it is age-restricted, `nsfw`.
- `harness.post` takes `files=`, and `harness.react` records the reaction on the fake message, so `reactors` sees it.
- `Guild.get_channel` and `get_text_channel` find threads. They did not, so a message or reaction in a thread led nowhere.
- In tests, a job a plugin schedules after `harness.advance` falls due when the clock passes its time. It came due later by the whole time advanced so far, so `run_jobs` missed it.

## 1.6.0 - 2026-11-16

### Using teabot

- The documentation has a new home page: three paths, Guild admins, Self-hosters and Plugin developers, with a button that adds Teabot to your guild, and a slowly turning fractal behind them. Every page's header has Plugin hub and Add Teabot buttons, and the home page a wide banner into the hub.
- The plugin hub is part of the documentation, in its style: a card for each plugin and a page for each, with an Enable in your server button. It follows the hub within the hour.
- Each plugin's card shows its icon, name and version, its description, and its author. The home page's three paths show Teabot's mascots, and the site has Teabot's new logo and favicon.

### Self-hosting

- `teabot hub outdated` and `sync` find a hub plugin that `teabot hub add` installed, which uv records as its name in `dependencies` and its commit in `[tool.uv.sources]`. They missed it before, and `sync` offered to install it again.
- The template download holds no `uv.lock`. `setup.sh` locks the newest teabot when there is none, and on a bare-metal host you run `docker compose run --rm uv lock` once before the first deploy, as the bare-metal guide says. A release's template is then on GitHub the moment the release is.

### Writing plugins

- The plugin hub requires a plugin to name its author in its `pyproject.toml` `authors`, and shows it on the plugin's card. See [Publishing a plugin](https://teabot-dev.github.io/teabot/plugins/publishing/).

## 1.5.0 - 2026-11-16

### Using teabot

- The dashboard: admins sign in with Discord at `/dashboard`, pick a server they manage, and turn its plugins on and off and change their settings, with the same permission checks and audit log as `/plugins`.

### For operators

- The template's `compose.yaml` and `teabot.service` gain a memory limit, `teabot.env.example` the watchdog's and the dashboard's settings, and `teabot.caddy` a route for `/dashboard`. Take them from the template to have them, as in [Upgrade](production.md#upgrade).

### Self-hosting

- `TEABOT_DISCORD_CLIENT_SECRET`, with `TEABOT_PUBLIC_URL`, turns on the dashboard. The bare-metal Caddy file forwards `/dashboard`. See [The dashboard](production.md#the-dashboard).
- `teabot hub sync` installs every plugin the hub lists, each at the newest reviewed version this teabot runs, for an instance that offers the whole hub to its servers. `--check` says what would change. `teabot hub add`, `show` and `outdated` also pick the newest reviewed version this teabot runs, since the hub's catalogue now lists every reviewed version with the teabot range it needs.
- A watchdog logs a plugin that holds the event loop for a second, as `event loop blocked` with the plugin, file and line, and stops the process after thirty seconds held, for Compose or systemd to restart it. `TEABOT_LOOP_WARN_SECONDS`, `TEABOT_LOOP_LIMIT_SECONDS` and `TEABOT_STARTUP_LIMIT_SECONDS` set the limits.
- A job that stops the process on every attempt is failed, with "the process stopped while running it", where it used to run again after every restart.
- A plugin in `TEABOT_DISABLED_PLUGINS` is no longer imported, so none of its code runs.
- `teabot init --rotate` gives the app and plugins roles new passwords, such as after a leak. The troubleshooting page has the steps for a plugin that may be harmful.
- The template caps the bot's memory at 1 GB, restarting it past that: `mem_limit` in `compose.yaml`, which `TEABOT_MEMORY_LIMIT` in `.env` changes, and `MemoryMax=` in the bare-metal unit.
- `/healthz` reports the memory the process uses now, as `memory_mb`. Action and job log lines carry `duration_ms`.

### Writing plugins

- An action is stopped after a minute and a job after fifteen, unless it sets `timeout=` in its decorator. A destructive action's apply has an hour.

## 1.4.0 - 2026-11-15

### Using teabot

- A confirmed change's reply follows it as it applies, showing its progress, such as `Applying: 3 of 10.`, and ends with `Done.` or the failure, instead of pointing at `/admin job_status`.
- The built-in commands suggest as you type: `/plugins enable` the plugins that are off, `/plugins disable` those that are on, `/plugins config` plugins and settings, `/tokens revoke` your tokens, and `/admin job_status` your latest jobs.
- Replies show times as Discord timestamps, in each member's own time zone, and channels, roles and members as mentions, where they showed raw values before.

### For operators

- Giveaways no longer ships with teabot: it is the first plugin on the [plugin hub](https://teabot-dev.github.io/plugin-hub/), as `teabot-giveaways`. An instance that uses it installs it from the hub with this release, `uv run teabot hub add giveaways`, before rebuilding. Its id is still `giveaways`, so its data, settings and scheduled draws carry on as they were.

### Self-hosting

- The [plugin hub](https://teabot-dev.github.io/plugin-hub/) lists reviewed plugins. `teabot hub search`, `show`, `add` and `outdated` find them, install one at its reviewed commit, and say when a newer one is reviewed.
- `uv run teabot dev` runs the bot from the instance folder and restarts it when a plugin's code, migrations or `teabot.env` change. It reaches the Compose Postgres through `.env`.
- `TEABOT_MESSAGE_CONTENT=true` turns on Discord's Message Content intent, for plugins that read what members write. It is in the template's `teabot.env.example`.
- `teabot users delete <id>` deletes a member's data on their request: their rows in every plugin, and their tokens. The audit log keeps what they did, without their ID.

### Writing plugins

- Plugin code sees only the current server's rows: every plugin table gets a row-level security policy on `guild_id`, and the current server as `guild_id`'s default, so an insert may leave it out. Plugin code runs as a new runtime role, which cannot create tables or other objects; migrations keep the owner role, which sees every server. Existing tables gain the policy and the default at the first start on this release.
- A column of members' IDs has the type `teabot.member_id`, or `teabot.member_id[]`, so deleting a member's data finds it. A migration that leaves such a column `bigint`, named like `member_id`, `user_id` or `winner_ids`, is refused with the type to use; alter it in a new migration. The polls tutorial and the scaffold use it.
- `ctx.store(Model)` keeps a server's pydantic records by key, text or a member, with no tables or migrations. Each plugin's schema gains a `teabot_store` table for them.
- `@plugin.action(menu="...")` adds a right-click command on a member, for an action taking a `Member`, or on a message, for one taking a `ReceivedMessage`. `harness.menu` chooses it in tests.
- `ctx.http` reaches the hosts in `Plugin(..., hosts=[...])` and refuses any other (D12). `teabot check` flags an HTTP library of the plugin's own, such as `requests` or `aiohttp`; move its requests to `ctx.http`. `harness.web` answers them in tests.
- `plugin.instance_settings(Model)` reads settings for the whole instance, such as an API key, from `TEABOT_PLUGIN_<ID>_<FIELD>` when the plugin loads.
- `@plugin.job(every=timedelta(...))` runs a job on its own, once an interval, in each server where the plugin is on.
- `@plugin.on_enable` runs when a server turns the plugin on, such as to create a channel or a role it needs. `harness.enable()` runs it in tests.
- `Select(..., defaults=(id, ...))` opens a role, channel or member picker with those chosen.
- `@plugin.on_message`, `on_reaction_add`, `on_reaction_remove`, `on_member_roles` and `on_voice` handle messages, reactions, role changes and voice moves, each as a job, like joins and leaves. A message's text needs `TEABOT_MESSAGE_CONTENT=true`. The harness fires them with `post`, `react`, `change_roles` and `voice`.
- `tx.send(channel, ...)` posts with the transaction: the post goes out a moment after the commit, and never if the block rolls back. It returns a `Post`, which `tx.edit` and `tx.delete` change later. The polls tutorial posts its results this way.
- `harness.call`, `press` and `choose` send the posts an action queued before they return.

## 1.3.0 - 2026-11-15

### Using teabot

- The roles plugin ships with teabot: `/roles post` puts up a role picker, with a button for each role, which members press to take the role and press again to give it back. It offers only roles that are safe to give out, refusing managed roles, roles with a moderator's permission, and roles at or above the bot's or the offering admin's highest role, and checks again on every press. `/roles add` and `/roles remove` update the post in place.

### Self-hosting

- `teabot run` starts on Windows. Its event loop has no signal handlers, so SIGINT and SIGTERM fall back to the process-wide handler, which still stops the bot cleanly.

### For operators

- Nothing to do by hand: the template is unchanged since 1.2.0. Upgrade teabot and rebuild.
- The roles plugin arrives with teabot, off in every server until an admin turns it on.

### Writing plugins

- Plugins can manage a server as its admins do. A `Member` can be given and lose roles, renamed, timed out, kicked and sent a direct message, and shows its roles, top role, join date, avatar and timeout. A `Role` shows its position, permissions, colour and whether Discord manages it. The `Guild` shows its icon, owner, member count, roles and every `Channel`, finds members by name, and bans, unbans and lists bans. Each acts as the bot, and refuses with a plain reason what Discord would refuse, such as a role above the bot's own.
- `Guild.me` is the bot itself, as a member, to compare roles with.
- `@plugin.action(surfaces=Surface.COMPONENT)` makes an action only its buttons and menus reach: no slash command and no MCP tool.
- A channel, role or member input or setting accepts a mention, such as `<#123>`, as well as the ID.
- `teabot new-plugin` writes a range starting at the running release, such as `teabot>=1.3,<2`, since a new plugin may use anything it added.
- Every change a plugin makes carries a reason to Discord's audit log, "via teabot: <action>, for member <id>", unless the plugin gives its own.
- `send` attaches files with `File`.
- Posts carry buttons: `Button(action.bind(...), label=, emoji=, style=)` runs that action as the member who presses it, through the same checks and audit log as a slash command. The call is held in the button itself, so it keeps working after a restart. The bot can fetch its own posts with `channel.get_message`, and edit or delete them.
- `harness.press(button)` presses a button on a fake message, and returns the reply the member would see.
- Inputs can be lists, such as `Annotated[list[Role], Field(max_length=5)]`. Discord shows one as numbered options, `roles_1` to `roles_5`, and MCP clients and the service API send an array.
- Posts carry select menus: `Select(action.bind(...), into="roles", ...)`, of `Option`s or of Discord's own role, channel or member pickers, fills that list input with what the member chooses. `harness.choose(select, values)` chooses in tests.
- Options suggest values as the member types: `@action.autocomplete("option")` attaches a function that receives what has been typed and returns `Choice`s. It runs as the member without the audit log; anything that would refuse the action, or a suggester that fails or takes past Discord's three seconds, suggests nothing. `harness.autocomplete(action, option, typed)` checks it.
- Plugins hear members joining and leaving: `@plugin.on_member_join` receives the `Member`, and `@plugin.on_member_leave` the `User` they were, with their ID and name. Only plugins enabled in that server hear it, and each event runs as a job of the plugin's, so a failure is retried and shows in `/admin job_status`. `harness.join(member)` and `harness.leave(member)` fire them in tests.
- An optional number keeps its limits on Discord: `Annotated[int, Field(ge=1, le=60)] | None` showed no limits before, so Discord let a member type any number for teabot to refuse.
- The test fakes do all of it, record what was done, and refuse as Discord would.
- The harness's first member owns its fake server, as a server's first admin usually does, so it may manage any role.
- The test fixtures' cleanup of killed runs works on Windows, where `os.kill(pid, 0)` raised an error for a dead process, and sent Ctrl+C to a live one, instead of probing it.

## 1.2.0 - 2026-11-14

### Documentation

- The documentation is a site, at https://teabot-dev.github.io/teabot/, in three parts: using teabot in a server, self-hosting, and writing plugins. Its tutorial builds a polls plugin, whose code is tested with teabot's own.

### Using teabot

- A plugin's command is shown only to members who hold the permissions all of its actions share, so most members no longer see commands they cannot use. teabot still checks each action's permissions on every call.
- `/tokens create` replies with the commands to paste into Claude Code, which read the token at a hidden prompt, and with the address and header any other MCP client needs.

### Self-hosting

- `./setup.sh` in the template sets up and starts an instance in one command: it asks for the bot token, writes `.env` and `teabot.env`, builds and starts the bot and Postgres, creates the database roles, and prints the invite link. It can be run again after a failure.
- The template's Compose file has a `uv` service, so changing an instance's plugins needs only Docker: `docker compose run --rm uv add teabot-foo`.
- The template's `.dockerignore` keeps `.env`, which holds the database owner's password, out of the image's build context.
- `teabot plugins purge` works in the bot's container, whose user has no name; the audit log records its id.
- `setup.sh` checks that it can reach Docker, and a failed health check says how to fix a wrong token.
- The template's `.gitignore` keeps database backups out of git, and the bare-metal setup installs Caddy and stops when `teabot init` fails.

### For operators

- To use `./setup.sh` and the `uv` service, copy `setup.sh`, `compose.yaml`, `.dockerignore` and `.gitignore` from the new template into your instance. Your `.env`, `teabot.env` and database are unchanged, and `setup.sh` keeps them.
- A bare-metal host gets Caddy from Ubuntu's packages when `setup-host.sh` runs again. A host that has Caddy already is unaffected.

### Writing plugins

- Plugin tests read the database password from the instance's `.env` themselves, so test commands need no `--env-file .env`.
- `teabot new-plugin` runs `uv lock` after writing the package; `--no-lock` skips it.

## 1.1.0 - 2026-11-14

### Writing plugins

- `Plugin(id, config=)` with `@plugin.action` and `@plugin.job` decorators. An action's inputs are its function's parameters, described by its docstring's `Args:` section, and its reply is its return value: a model, a list of models or a string. The tutorial builds a plugin with them step by step.
- `Context[Config]` gives actions and jobs `guild`, `actor`, `config` as the plugin's own model, `db`, `now`, `diff` and `report`.
- `TextChannel`, `Role` and `Member` parameters arrive as the Discord objects, checked to exist. The SDK's own `Guild`, `TextChannel`, `Member`, `Role` and `Message` protocols are implemented by discord.py's objects and by the test fakes alike.
- `needs=` takes Discord permission names, which pyright checks. `reply="public"` shows a reply to the channel.
- Typed queries: `fetch(Model, sql, ...)`, `fetchrow(Model, ...)` and `fetchval(int, ...)` validate each row.
- Typed, keyed jobs: `tx.schedule(job.bind(...), at=...)` checks the arguments against the job's function, scheduling the same bound job again moves it, and `tx.unschedule` removes it. Jobs carry the member who started them, and get the server's config.
- A destructive action's apply reads the confirmed plan as `ctx.diff`, whose entries carry a `key`.
- Messages mention nobody unless `ping=` names members.
- When a plugin loads, teabot imports every module of its package, and checks its schema: foreign keys and unique indexes include `guild_id`, and there are no views or `SECURITY DEFINER` functions.
- `teabot check` runs the review checklist's mechanical items: imports only from `teabot.sdk`, the clock read only through `ctx.now` and `now()`, no `SET` in SQL, and a README with "External connections".
- `teabot new-plugin` writes a one-file plugin on the decorators, with typed tests and a snapshot test.
- The `Plugin` protocol for objects with a `manifest` and `setup` is now `LoadablePlugin`. Such plugins load as before.

### Testing plugins

- `harness.call(action, ...)`, typed by the action, with `as_member(member)`, `plan`, `confirm`, `configure`, `run_job` to run a job again, and `advance`, which moves `ctx.now`, `now()` in the plugin's SQL, and when jobs fall due.
- The harness lints the plugin's package when it loads it.
- `teabot_snapshot` keeps a plugin's commands, MCP tools and job payloads in a committed file, and `pytest --teabot-accept` accepts a change.
- `HarnessFactory` types the `teabot_harness` fixture. Fake roles, and members with roles.

### Plugins

- The first-party plugins ship inside teabot, as RuneLite ships its core plugins in the client. Each is still off in a server until `/plugins enable`, and loads through the same entry points as any other plugin.
- giveaways: start a giveaway in a channel, let members enter, and draw the winners when time is up.
- announcements is written on the decorators. Announcements mention nobody, so an `@everyone` in the text stays plain text.
- `teabot new-plugin` refuses an id an installed plugin already has.

### For operators

- Remove `teabot-announcements` from your instance's dependencies, since teabot now carries it: `uv remove teabot-announcements`, then `uv lock --upgrade-package teabot`. With both installed, two plugins share the id `announcements`, and neither loads. Its config, tables and queued jobs carry over, since the id is unchanged.

### Operations

- `/admin job_status` shows a job's label and who started it.
- The release workflow publishes the GitHub release as a draft until its template is attached, and waits up to an hour for PyPI's index.

## 1.0.2 - 2026-11-14

Fixes from the first plugin authoring trial.

- Plugins can use array columns and array parameters. They failed with "permission denied for function set_config", because asyncpg calls it before looking up an array type.
- A failed job names its error and traceback in the log, and `harness.run_jobs()` raises the new `teabot.testing.JobFailed`, with the job's own exception as its cause.
- An integer field's `ge`, `le`, `gt` and `lt` become the slash option's minimum and maximum, so Discord checks them before sending.
- Without Postgres, the test fixtures stop at once and say how to start it, instead of retrying for a minute.
- `teabot new-plugin` declares a compatible teabot range, such as `teabot>=1.0,<2`.
- The `ctx.db` docstrings no longer claim that rows are filtered by server: every query must filter by `guild_id`.
- The plugin guide answers what the trial had to read teabot's code for: posting, config, confirming plans and making jobs due in tests, the retry schedule, and the refusal codes.
- asyncpg is pinned below 0.33.

## 1.0.1 - 2026-11-14

- A new README with the teabot icon and badges. The design decisions and the architecture moved into documents of their own.
- PyPI shows the project's links, keywords and classifiers.

## 1.0.0 - 2026-11-14

The first release.

- A plugin framework for Discord bots. Each action a plugin offers is a slash command, an MCP tool and a service API route at once, behind one call path that checks the member's Discord permissions on every call and records an audit trail.
- `teabot.sdk` 1.0: actions, jobs, per-server config, each plugin's own tables in its own schema and role, and plan then apply for destructive actions.
- Tokens for LLM clients and event backends, tied to a member and narrowed by risk and by action.
- Servers that add and remove the bot, with their data kept for a grace period and then purged.
- `teabot-announcements`: post an announcement now, or schedule it.
- `teabot init`, `teabot run`, `teabot plugins list | purge` and `teabot new-plugin`.
- The instance template, with a quick setup on Docker Compose and a careful one on a bare-metal host with continuous backups.

<!-- --8<-- [end:releases] -->
