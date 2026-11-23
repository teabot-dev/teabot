# Architecture

How teabot is built: the concrete shape behind the decisions in [design.md](design.md). Numbers in brackets, such as (D16), point at those decisions.

## Repositories

- **This repository is the framework (D42).** A uv workspace. It publishes one package, `teabot`, to PyPI, and holds the instance template. The first-party plugins ship inside `teabot` as `teabot.plugins.<id>`, the way RuneLite ships its core plugins in the client. They register through teabot's own `teabot.plugins` entry points, so they load exactly as a third-party plugin does (D4).
- **Each instance is its own repository, made from the template.** The Tea clan's instance is the first. It lists its third-party plugins in its own `pyproject.toml`, pins them in its own `uv.lock`, and keeps its deploy files and any local plugins of its own.

```text
pyproject.toml                  workspace root, the `teabot` script, a `dev` dependency group (pytest, pytest-postgresql, ruff, pyright, import-linter, teabot-devtools)
LICENSE                         the MIT licence of D41
CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, CHANGELOG.md   (D48)
.github/
    pull_request_template.md    plugin checklist: external connections (D12), migrations that keep the previous release working, async I/O, jobs safe to run twice
    workflows/                  CI (tests, ruff, pyright, import-linter) and releases to PyPI through trusted publishing
src/teabot/
    sdk/                        the public plugin API, semantically versioned (D45)
    testing/                    fakes and the Postgres fixture for plugin tests (D45)
    __main__.py                 `teabot run`: settings, the shard check, migrations under the advisory lock, plugin loader, then the bot
    cli/                        `init`, `new-plugin`, `plugins list`, `plugins purge` (D34)
    settings.py                 pydantic-settings, read from the env file
    app.py                      runs discord.py, uvicorn, the queue workers and the lock connection in one asyncio loop
    core/
        actions.py              the registry and the call path
        loader.py               entry-point discovery or a given plugin list, id rules, disabled plugins, failure isolation (D13, D14, D46)
        lifecycle.py            per-guild enablement, plugin purge and guild removal (D7, D49)
        permissions.py          default permission checks, with the place for later overrides (D19)
        audit.py                audit rows, retention and the `post_audit` handler (D24)
        plans.py                canonical diffs, plan rows and confirm checks (D21)
        queue.py                the durable work queue, its workers and LISTEN/NOTIFY wake-ups (D31)
        config.py               per-guild plugin config (D10)
        processes.py            the lock connection, shard and plugin advisory locks, the informational process row (D40)
    adapters/
        schema.py               one builder from input models to MCP JSON schema and Discord command JSON (D29)
        interactions.py         the interaction router for commands, components and modals (D32)
        commands.py             per-guild command sync jobs and hashes (D32)
        render.py               default output embeds (D29)
        mcp.py                  actions to MCP tools on the SDK's low-level server
        service_api.py          the REST route for actions
    auth/
        tokens.py               issuing, hashing, checking and revoking tokens (D16)
        middleware.py           bearer-token check for `/mcp` and `/api`
    db/
        pool.py                 the core and plugin pools, role switching on acquire, transaction handles, the open-block check (D30)
        migrate.py              the migration runner, plugin schemas and roles, the guild_id check (D9)
        migrations/             the core's numbered SQL files, for the `core` schema
    builtin/
        admin.py                the core admin plugin (always enabled)
    plugins/                    the first-party plugins, each importing only `teabot.sdk`
        announcements/          the first plugin (D50)
        giveaways/              the tutorial's worked example, with its migrations/
plugins/
    devtools/                   dev-only plugin that exercises own tables, failure and plans, package `teabot-devtools`
template/                       the instance template (D43)
    pyproject.toml              depends on `teabot` and any third-party plugin packages
    plugins/                    where an operator's own plugins live as workspace members (D44)
    teabot.env.example          names of every setting, no values
    compose.yaml                the bot and Postgres, for the quick setup
    Dockerfile                  builds the instance with `uv sync --frozen --no-dev --all-packages`
    deploy/bare-metal/          the Tea droplet's files as the careful example (D37)
        teabot.service          hardened systemd unit, `ExecStart` runs `uv run teabot run`
        teabot.caddy            Caddy site block, forwarding `/mcp` and `/api` only
        postgresql.conf.d/teabot.conf   memory settings, WAL archiving through pgBackRest
        pgbackrest.conf         the Spaces repository, schedules and retention
        setup-host.sh           one-time install: Postgres, pgBackRest, Caddy, then `teabot init`
        deploy.sh               pull, `uv sync --frozen --no-dev --all-packages`, restart, health check on localhost
    README.md                   creating the Discord application, intents, one teabot per server, both setups
tests/                          core tests
docs/                           the documentation site's pages, built with Zensical (`zensical.toml`)
    use/                        using teabot in a server
    host/                       running an instance
    plugins/                    writing plugins
examples/ballots/               the tutorial's plugin, tested like any other
design/
    design.md                   the numbered design decisions
    architecture.md             this file
    spikes.md                   the experiments that settled open technical questions
    glossary.md                 the terms the design and the code use
```

## Plugin lifecycle

The six steps of D7. Install, upgrade, uninstall and block change files on the hosts and take effect at restart (D15).

| Step | Who | Database | Config and jobs | Commands |
|---|---|---|---|---|
| Install or upgrade | operator, in the instance repo and a deploy | schema and role created if new, pending migrations run at start | kept, config read through the new model (D10) | synced where enabled |
| Enable in a server | server admin | no change | config defaults apply, jobs resume | synced to that server |
| Disable in a server | server admin | no change | kept, jobs pause | removed from that server |
| Uninstall | operator, in the instance repo and a deploy | no change | kept, jobs pause | removed everywhere |
| Block | operator, `TEABOT_DISABLED_PLUGINS` and restart | no change | kept, jobs pause | removed everywhere |
| Purge | operator, `teabot plugins purge` | schema and role dropped | every row deleted | removed everywhere |

- **Plugin status.** Each process keeps each known plugin's status in memory: `loaded`, or `not_loaded` with a reason of `blocked`, `uninstalled`, `failed` or `duplicate_id`. Known plugins are those with entry points plus those with rows in `core.migrations` or `core.guild_plugins`. A plugin that is not loaded keeps its data, has no commands and cannot be enabled. `/healthz` and `/plugins list` report the statuses, and every loaded plugin shows in `pg_locks` through the shared lock its process holds (D40).
- **Uninstalling.** Removing the package leaves its schema, role and rows in `core.guild_plugins`, `core.jobs` and `core.migrations`. A reinstall picks them up again.
- **Purging.** Works with or without the package installed, and with the bot running.
    1. Take the plugin's advisory lock exclusively with `pg_try_advisory_lock`. Refuse when that fails, because some process has the plugin loaded. Hold the lock until the purge ends, so no process can load the plugin in the meantime.
    2. Show the tables and row counts it will delete and ask for confirmation.
    3. In one transaction: `DROP SCHEMA plugin_<id> CASCADE`, `DROP ROLE teabot_plugin_<id>`, and delete the plugin's rows in `core.guild_plugins`, `core.jobs` and `core.migrations`.
    4. Queue command syncs for the servers that had it enabled, and write an audit row with the operator's system user.

## Guild removal (D49)

- `core.guilds` records each server the bot is in, with joined at, removed at and the id of any pending `purge_guild` job.
- On the guild-remove event, the core sets removed at and queues `purge_guild` for `TEABOT_GUILD_RETENTION_DAYS` later, 30 by default. Jobs for that guild pause.
- On a guild-join event for a server with removed at set, the core clears it, cancels the pending job and resumes the guild's jobs. Its config, tokens and plugin data are as they were.
- `purge_guild` runs one transaction per plugin schema and one for the core. Each deletes `WHERE guild_id = $1` from every table, which the `guild_id` check of D9 guarantees exists. Audit rows for the guild are deleted with the rest.
- A guild that is removed while the bot is down is found at startup, by comparing `core.guilds` with the guilds the gateway reports.

## Plugin data

### Config (D8, D10)

```python
from teabot.sdk import ChannelRef

class GiveawayConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    channel_id: ChannelRef | None = None
    default_duration_minutes: int = 60
```

- `core.guild_plugins` stores each server's config as JSONB for each plugin. Admins edit it with `/plugins config`, and the action receives it validated as `ctx.config`.
- Config is what admins set. A plugin never writes to its own config. Anything the plugin writes goes in its own tables.
- The core's own per-server settings, such as the log channel, are the admin plugin's config and follow the same rules.
- On load, the JSON is validated against the plugin's current model. Defaults, `AliasChoices`, `extra="ignore"` and `model_validator(mode="before")` cover every change a plugin makes to its config.
- Each plugin's tests keep stored config samples from earlier versions in `tests/fixtures/` and check that they still validate. A breaking model change then fails in CI before a deploy.

### Schemas, roles and migrations (D9)

- The database has an owner role, used only by the migration runner, and two login roles for the pools: `teabot_app` for the core and `teabot_plugins` for plugin code. `teabot_plugins` has no rights of its own. `teabot init` creates all three, and revokes `EXECUTE` on `pg_catalog.set_config` from `PUBLIC`, granting it back to `teabot_app` and the owner. That step needs a superuser once, and on managed Postgres without one, `teabot init` warns that the injection limit of D9 falls back to review.
- Each plugin adds a role `teabot_plugin_<id>` with `NOLOGIN`, granted to `teabot_plugins` so the plugin pool can switch to it.
- The runner also creates `plugin_<id>.schedule_job(guild_id, handler, run_at, payload)`, owned by `teabot_app`, `SECURITY DEFINER`, with `SET search_path = core, pg_temp` and the plugin id written into its body, and grants `EXECUTE` only to the plugin's role. A plugin queues its own jobs through it and cannot read or replace it (S1).
- When a plugin first appears, the runner creates its schema `plugin_<id>`, owned by its role, and grants the role nothing else. The plugin role has no rights on `core` or on any other plugin's schema, so a query or foreign key that reaches outside fails with a permission error.
- At start, `teabot run` takes `pg_advisory_lock` on a fixed key, so processes starting together migrate once. The others wait and then find nothing to do.
- For each installed plugin, in any order since plugins share nothing, the runner applies every file above the plugin's recorded version. Each file runs in its own transaction:
    1. `SET LOCAL ROLE teabot_plugin_<id>` and `SET LOCAL search_path TO plugin_<id>`.
    2. The file, sent whole through asyncpg's simple query protocol. Dollar-quoted functions and triggers work because nothing splits the file.
    3. `RESET ROLE`, then a check against `information_schema.columns` that every table in `plugin_<id>` has a `bigint` column `guild_id`. A missing column raises and rolls the file back.
    4. The insert into `core.migrations`.
- A failed file rolls back, leaves the plugin not loaded with the reason `failed` (D14) and leaves the other plugins migrating as normal.
- The runner refuses a plugin whose recorded version is above its highest file. That means a downgrade, and migrations only go forward.
- **Rolling restarts.** A one-process instance stops, migrates and starts, so two releases never share a schema. The authoring guide still recommends that a release's migrations keep the previous release working, with columns and tables added in one release and dropped in a later one. The rule becomes required, and the PR template checks it, when shard clusters bring rolling restarts (deferred work).

### Database access at runtime (D30)

```python
@plugin.action()
async def enter(ctx: Ctx, giveaway: int) -> str:
    member = await ctx.guild.get_member(ctx.actor.user_id)     # Discord call, outside any transaction
    async with ctx.db.transaction() as tx:                      # pooled connection as teabot_plugin_giveaways, BEGIN
        await tx.execute(ENTER, ctx.guild.id, giveaway, ctx.actor.user_id)
        count = await tx.fetchval(int, COUNT, ctx.guild.id, giveaway)   # same connection, sees the insert
        await tx.schedule(remind.bind(giveaway=giveaway), at=ctx.now + timedelta(hours=1))
    return f"You're in, with {count} others."
```

- Each process holds two asyncpg pools: the core pool logged in as `teabot_app`, and the plugin pool logged in as `teabot_plugins`. teabot connects to Postgres directly, and the pools are the only poolers.
- When a plugin acquires a connection from the plugin pool, the core runs `SET ROLE teabot_plugin_<id>` and sets `search_path` to the plugin's schema. On release it runs `RESET ROLE` before asyncpg's own reset, which resets `search_path` but leaves the role in place (S1). Plugin SQL names its tables without a prefix, and a single read outside a block needs no transaction.
- Every `ctx.db` and `tx` helper runs its statement as a prepared statement, with parameters bound separately, so one call runs exactly one statement. Argument-less `execute` is never used, because asyncpg sends it through the simple protocol, which runs several. Row counts come from the prepared statement's status message.
- With `set_config` revoked and a session user that has no rights, an injection bug can neither change the role nor reach `core` (D9). Plugin code that changes the role on purpose is a review matter (D5).
- `ctx.db.transaction()` yields `tx`, and everything inside the block goes through it. The block sets a context variable that only marks it as open. Calling `ctx.db` while it is set raises an error naming `tx`, because that call would take another connection and miss the block's writes.
- Inside a block, a plugin only runs SQL. Awaiting Discord or HTTP there would hold a pooled connection and its row locks across a slow call. Dev mode logs any block held longer than 100 ms, and the authoring guide states the rule.
- `tx.schedule(bound, at=...)` calls the plugin's own `schedule_job` function, so a job is queued in the same transaction as the writes that need it. A bound job's key is its handler and canonical JSON arguments. The partial unique index `jobs_pending_key` allows one pending job per key, so scheduling a key again moves the pending job, and `tx.unschedule(bound)` deletes it.
- Typed queries, `fetch(Model, sql, ...)`, validate each row through the model, or through a `TypeAdapter` for a plain type such as `int`.

## The plugin SDK (D45)

Everything here is importable from `teabot.sdk`, and nothing else in `teabot` is part of the plugin API. An `import-linter` contract in CI fails any first-party plugin that imports from elsewhere in `teabot`, and `teabot check` does the same for any plugin. Later additions arrive as fields with defaults and optional protocols (D6).

The SDK has two layers. Plugins write the upper one, and the core sees only the lower one.

- **Decorators.** `Plugin(id, config=)` is generic over its config model. `@plugin.action(risk=, needs=, name=, reply=)` turns an async function into an `Action`, and `@plugin.job` into a `Job`.
    - The input model is built from the parameters after the context, with `create_model`. The docstring's summary and `Args:` section give the command's and every option's description, and a missing one fails at decoration time with the file and line (S7).
    - `TextChannel`, `Role` and `Member` parameters are stored as `ChannelRef`, `RoleRef` and `MemberRef` IDs, and resolved through the guild before the function runs.
    - A `str` or `list[Model]` return type is wrapped in a generated output model, and a model's own `render()` becomes the spec's `render`.
    - `needs` is a `Literal` of discord.py's permission names, so pyright checks it. `Action.bind` and `Job.bind` carry the function's `ParamSpec`, so `harness.call`, `.bind` and `tx.schedule` are checked against the function's signature.
    - `Context[C]` is a protocol over the lower layer's contexts: `guild`, `actor`, `config: C`, `db`, `now`, `diff` and `report`. `ctx.guild` is a view that satisfies the SDK's own `Guild` protocol, with `TextChannel`, `Role`, `Member` and `Message` protocols beside it, which the adapter's discord.py objects and the test fakes both implement. Sends mention nobody unless `ping=` names members.
    - `ctx.now` reads one clock, which the test harness moves. In the test database the plugin's schema holds a `now()` that reads the same clock, ahead of `pg_catalog` on the `search_path`.
    - When the plugin is set up, each `Action` becomes an `ActionSpec` and each `Job` a handler. The loader first imports every module of the plugin's package, so decorated functions in any module register.
- **Specs.** The shapes below, which any object with a `manifest` and a `setup` can register directly (`LoadablePlugin`).

```python
class Risk(IntEnum):
    READ = 1         # no side effects
    WRITE = 2        # side effects that are easy to undo (post a message)
    DESTRUCTIVE = 3  # bulk or hard to undo, always plan then apply (D21)

class Surface(Flag):
    SLASH = auto()
    MCP = auto()
    SERVICE = auto()
    ALL = SLASH | MCP | SERVICE

@dataclass(frozen=True)
class Actor:
    user_id: int                                     # a member (D16), or SYSTEM for a job nobody started
    via: Literal["slash", "component", "mcp", "service", "job", "event"]
    token_id: str | None = None                      # set for MCP and service calls

@dataclass(frozen=True)
class ActionSpec[I: BaseModel, O: BaseModel]:
    name: str                          # "announcements.post", unique across plugins
    summary: str                       # slash description and MCP tool description
    input: type[I]
    output: type[O]
    default_permissions: discord.Permissions
    risk: Risk
    run: Callable[[ActionContext, I], Awaitable[O]]
    surfaces: Surface = Surface.ALL
    plan: Callable[[ActionContext, I], Awaitable[BaseModel]] | None = None  # returns the diff; required when DESTRUCTIVE
    render: Callable[[O], discord.Embed | str] | None = None               # slash display; default is an embed of the fields (D29)

class ActionContext:
    guild: discord.Guild        # the one guild this call is about (D2)
    actor: Actor
    db: Database                # transaction() yielding tx, and query helpers, running as the plugin's role (D30)
    config: BaseModel           # this plugin's config for this guild (D10)
    queue: JobQueue             # schedule jobs for this plugin and guild (D31)
    audit: AuditWriter

class Registrar(Protocol):
    def action(self, spec: ActionSpec) -> None: ...
    def on_event(self, event: str, handler: EventHandler) -> None: ...      # "member_join", ...
    def component(self, name: str, handler: ComponentHandler) -> None: ...  # custom id teabot:<plugin>:<name>:<payload>
    def job(self, name: str, handler: JobHandler) -> None: ...               # target of scheduled jobs

@dataclass(frozen=True)
class Manifest:
    id: str                              # matches [a-z][a-z0-9_]{1,31}; the schema suffix and command group (D46)
    version: str
    config: type[BaseModel] = EmptyConfig
    migrations: Path | None = None       # own tables (D9)

class LoadablePlugin(Protocol):
    manifest: Manifest
    def setup(self, registrar: Registrar) -> None: ...
```

- **Versioning.** `teabot.sdk` follows semantic versioning from 1.0. A removal or an incompatible change needs a major version, after at least one minor release in which the old form works and warns. Additions under D6 are minor releases.
- **Testing helpers.** `teabot.testing` provides fake guilds, members, roles and channels, and a `teabot_harness` fixture that creates a throwaway database, runs the plugin's migrations, loads it through the real loader and lints its package. `harness.call(action, ...)` runs the real call path against the fakes, `plan` and `confirm` drive plan then apply, `advance` moves the clock, and `teabot_snapshot` keeps the plugin's commands, tools and job payloads in a committed file.
- **One call path.** Every adapter builds an `ActionContext` and calls `registry.invoke(name, guild_id, actor, raw_input)`. Slash calls take the guild from the interaction, and token calls take it from the token (D20). Its steps:
    1. Resolve the guild. Refuse when the bot is not in it.
    2. Refuse when the front door is not one of the action's surfaces (D1). A slash-only action, such as everything under `/plugins` and `/tokens`, is therefore out of any token's reach.
    3. For token calls, refuse when the action's risk is above the token's maximum risk (D17), or when the token has an allow-list without this action (D20).
    4. Refuse when the plugin is not loaded or not enabled in that guild.
    5. Validate the input against the Pydantic model.
    6. Check permission: `default_permissions` against the member's current permissions, from the cache with a REST fetch when the member is not cached (D18). Per-server overrides (D19) slot in here when they arrive.
    7. For `WRITE` and `DESTRUCTIVE`, write the audit row with the outcome `started` (D24).
    8. For `DESTRUCTIVE`, see plan then apply below. Otherwise run the action.
    9. For `READ`, write one row to `core.audit_reads` with its outcome. For the others, in one transaction, record the outcome on the audit row and queue its `post_audit` job.
- **Plan then apply (D21).** Every `DESTRUCTIVE` action's input schema gains an optional `confirm: str` field, added by the core.
    1. Without `confirm`, the core calls `plan` and serialises the diff in canonical form (sorted keys and sorted lists). The audit row gets the outcome `planned`, the SHA-256 of the canonical diff and a random confirm value. The response holds the diff's counts, its first page of entries and the confirm value.
    2. With `confirm`, the core finds the `planned` row by its confirm value and refuses unless the actor, guild, action and input match and the row is younger than 15 minutes. It calls `plan` again and compares hashes. On a match it queues the core `apply` handler, which calls `run` and writes progress to the job, and the response holds the job id. On a mismatch it refuses and returns a fresh plan. The apply's audit row stores the plan row's id.
- **Interactions (D32).** One handler receives every interaction.
    - An application command is looked up by its command and subcommand names. Options arrive as plain values and IDs and are validated into the input model.
    - The adapter defers before calling the action and sends the result as a follow-up, through the action's `render` or the default embed. A command with a `LongText` field first answers with a modal holding that field, and defers once the modal is submitted.
    - A component or modal submit is looked up by its custom id, `teabot:<plugin id>:<name>:<payload>`. The core's own components use the plugin id `core`.
    - A destructive command's follow-up shows the diff with a Confirm button whose custom id is `teabot:core:confirm:<confirm value>`. The confirm value is 22 random URL-safe characters, which fits Discord's 100-character limit. Pressing the button calls the action again with that value, through the same confirm checks as MCP and the service API.
- **Discord objects in inputs.** Annotated types such as `ChannelRef`, `RoleRef` and `MemberRef` hold IDs. The schema builder maps them to Discord's channel, role and user option types and to ID strings in the MCP schema. An LLM finds IDs with the admin plugin's `describe_guild` and `find_members` actions.
- **Untrusted text in outputs (D23).** Output models type member-written text as `UntrustedText`. The MCP adapter wraps every such field in an explicit untrusted marker, so plugin authors cannot forget it.
- **Event hooks receive a guild-scoped context too.** A hook only runs for guilds where its plugin is enabled.
- **Loading (D14, D46).** `load(plugins=None)` reads entry points, or takes a given list so unit tests can pass fake plugins without packaging them. It then:
    1. Refuses any manifest whose id breaks the pattern or is reserved (`core`, `admin`, `teabot`, `plugin`, and the admin command groups `plugins`, `audit` and `tokens`), with the reason `failed`.
    2. Finds ids declared by more than one package, and loads none of those packages, with the reason `duplicate_id` and both package names in the error.
    3. Skips plugins named in `TEABOT_DISABLED_PLUGINS`.
    4. Runs migrations, then checks the plugin's schema against the database: foreign keys and unique indexes that include `guild_id`, no views or materialized views, and no `SECURITY DEFINER` functions. A broken rule fails the plugin with the constraint to write instead.
    5. Imports the package's modules and calls `setup`. Any exception leaves that plugin not loaded with the reason `failed`, and loading carries on.

## Writing a local plugin (D44)

The path a self-hoster follows, taught in the documentation site's Writing plugins section:

1. In the instance repo, run `uv run teabot new-plugin raffles`. It creates `plugins/raffles/` as a workspace member with its entry point, a one-file plugin, first migration, README, tests and snapshot test, and adds it to the instance's dependencies.
2. Write actions against `teabot.sdk`, and test them with `teabot.testing`.
3. Restart the instance. The loader finds the new entry point, creates `plugin_raffles` and its role, runs the migration and loads the plugin.
4. `/plugins enable plugin:raffles` in a server.

Nothing in this path contacts the teabot maintainers. Publishing the plugin to PyPI or a git repository, so other operators can install it, is the author's choice.

## Core tables

All in the schema `core`. Plugin roles and `teabot_plugins` have no rights on it. Plugins queue jobs through the `schedule_job` function in their own schema.

| Table | Holds |
|---|---|
| `core.migrations` | plugin id, version, file name, applied at (D9) |
| `core.guilds` | guild id, joined at, removed at, pending purge job id (D49) |
| `core.guild_plugins` | guild id, plugin id, enabled, config JSONB, updated by, updated at |
| `core.audit_log` | id, time, guild id, user id, token id, surface, action, input JSONB, risk, outcome (`started`, `planned`, `ok`, `refused`, `error`), error, diff hash, confirm value, plan id (a reference to the plan's row), job id. A plain table for `WRITE` and `DESTRUCTIVE` actions (D24) |
| `core.audit_reads` | time, guild id, user id, token id, surface, action, input JSONB, outcome, error. Partitioned by day on time, for `READ` actions (D24) |
| `core.jobs` | job id, guild id, plugin id (or `core`), handler, run at, payload JSONB, state, lease until, attempts, last error, progress, started by (D31) |
| `core.tokens` | token id, secret hash, guild id, user id, label, maximum risk, allowed actions (empty for all), created at, expires at, last used, revoked (D16) |
| `core.command_sync` | guild id, command-set hash, synced at (D32) |
| `core.processes` | process id, host, shard ids, shard count, version, plugin statuses, started at, updated at. Informational only (D40) |

- The core's `maintenance` job creates the next seven days of `core.audit_reads` partitions and drops partitions past retention. It deletes `core.audit_log` rows past retention in batches of a few thousand.
- `core.jobs` has a partial index on `(run_at)` where the state is pending, so claiming stays fast with millions of finished rows. Finished jobs older than 30 days are deleted in batches by the same job.
- `core.jobs` also has a unique partial index on `(guild_id)` where the handler is `apply` and the state is running. It allows one running apply per guild (D21).

## Processes (D40)

- Each process opens one dedicated connection outside the pool, the lock connection. It must stay outside the pool, because asyncpg's pool reset releases every advisory lock a pooled connection holds.
- Before it connects to the gateway, the process takes `pg_try_advisory_lock(<shard namespace>, <shard id>)` on the lock connection for each shard it will run. If any lock is taken, it releases what it has and refuses to start, naming the process row of the current owner.
- After loading plugins, it takes `pg_try_advisory_lock_shared(<plugin namespace>, <plugin key>)` for each loaded plugin, where the key is the first four bytes of the SHA-256 of the plugin id, read as a signed 32-bit integer. The key is computed in Python because Postgres's `hashtext` is not promised to stay stable across major versions (S2). Purge asks for the same key exclusively (D7).
- Reading the locks back from `pg_locks` converts `objid` from its unsigned form, and maps keys to plugin ids by hashing the known ids.
- A dropped lock connection is detected with asyncpg's termination listener, which makes the process exit.
- The lock connection also runs `LISTEN teabot_jobs` for the queue (D31).
- When the lock connection drops, the process exits with an error, and systemd or Compose restarts it. A crash releases every lock at once, so there are no staleness thresholds and no cleanup of stale owners.
- `teabot plugins list` reads the plugin locks from `pg_locks` to show what is loaded, and joins the informational `core.processes` rows for hosts and versions. The process updates its row every minute and deletes it on a clean shutdown.

## The work queue

- Each process runs a small pool of workers, eight by default.
- A worker claims one due job with `SELECT ... FOR UPDATE SKIP LOCKED`, sets it running with a lease, runs it and records the outcome. Two workers never claim the same job, in one process or across many.
- A process that runs only some shards adds `((guild_id >> 22) % $shard_count) = ANY($shards)` to the claim. The shard is computed there and never stored, so resharding leaves nothing stale. A process that runs every shard leaves the filter out.
- Inserting a job due now sends `NOTIFY teabot_jobs`. The lock connection listens (D40) and wakes the workers. Workers otherwise sleep until the next `run_at`, and poll every five seconds as a fallback for notifications lost during a reconnect.
- Setting an `apply` job running fails on the unique index when another apply is running for that guild. The worker then puts the job back with a short delay. The claim stays generic, and nothing is held across the apply's run.
- Every job is a handler, a payload and an optional key, and carries the member who started it. Plugins schedule with `tx.schedule(job.bind(...), at=...)`. The core's handlers run under the plugin id `core`: `apply` (D21), `post_audit` (D24), `sync_commands` (D32), `purge_guild` (D49) and `maintenance`.
- A failed job retries with exponential back-off up to a limit, then stays failed with its error for the operator to see.
- A job whose worker died is reclaimed once its lease runs out, so handlers must be safe to run twice. The authoring guide says so, and `post_audit` carries the audit row id so a repeated post is detectable.
- Jobs for a plugin that is disabled or not loaded, or for a guild the bot has left, stay paused. Purges delete them.

## Tokens

- **Issuing.** `/tokens create` in a server, with a label, a maximum risk (`write` by default), an expiry (30 days by default) and an optional list of allowed actions.
    - Any member can create tokens for themselves. A token acts as its creator and never holds more than the creator's live permissions allow (D18).
    - An event backend gets a token made by a staff member, labelled after the backend and narrowed to its actions (D20).
    - The full token is shown once in an ephemeral reply. Only the SHA-256 hash of the secret is stored.
- **Checking.** The middleware splits `teabot_<token id>_<secret>`, finds the row by id, compares hashes in constant time, and refuses expired or revoked tokens. It updates `last used` at most once a minute per token, so a busy token does not write on every call, and hands the actor and guild to the adapter.
- **Connecting Claude Code.** `claude mcp add --transport http teabot https://<host>/mcp --header "Authorization: Bearer teabot_..."`, one entry per server the staff member works in (D20).

## Front doors

- **Slash commands** (D29, D32):
    - One command group per plugin, with one subcommand per action, for example `/announcements post`.
    - Discord allows 100 top-level commands and 25 subcommands per group, so grouping by plugin keeps well inside the limits.
    - The command JSON comes from the schema builder, and replies follow the interaction rules in the SDK section.
    - **Per-guild command sync.** The core admin commands are global, so every server can always reach `/plugins`. Each plugin's group is registered as a guild command only in guilds where it is enabled.
        1. On enable, disable, purge, a plugin version change or startup, the core computes each affected guild's command-set hash and compares it with `core.command_sync`.
        2. For each guild whose hash changed, it queues a `sync_commands` job. The job sends one bulk overwrite for the guild, and saves the new hash only when Discord accepts it.
        3. Startup costs nothing for unchanged guilds. An upgrade that changes a command in thousands of guilds runs as thousands of paced jobs, which discord.py keeps inside the global rate limit.
        4. Whether an overwrite of a command with an unchanged name counts against Discord's 200 command creates per guild per day is unconfirmed. The hash check keeps overwrites to real changes either way.
- **MCP** (D28):
    - Served at `/mcp` over streamable HTTP, through the SDK's low-level server.
    - One tool per action with the `MCP` surface, named `announcements_post`. Its input schema comes from the schema builder. No tool takes a guild id, because the token decides the server (D20). The adapter has no tools of its own, since the core's lookups are admin plugin actions.
    - `tools/list` is filtered per token: only actions in plugins enabled in the token's server, up to the token's maximum risk, and within its allow-list if it has one.
    - Tool annotations come from the risk: `readOnlyHint` for `READ`, `destructiveHint: false` for `WRITE` (the spec treats a tool without the hint as destructive) and `destructiveHint` for `DESTRUCTIVE` (D22).
    - The server's `instructions` and every destructive tool description tell the LLM to show the diff to its user and get agreement before calling the tool again with `confirm` (D21).
    - The SDK's transport security allows the public hostname, since the reverse proxy forwards it. The SDK checks the raw `Host` header, so the proxy must keep it (S5).
    - Stateless mode with JSON responses, so any process can answer any request (D40). Refusals and action errors come back as `isError` results, since a raised exception would be a protocol error (S5).
- **Service API** (D20):
    - Requests carry `Authorization: Bearer teabot_<token id>_<secret>`.
    - `POST /api/v1/actions/<action name>` with `{"input": {...}}`, including `confirm` for a destructive apply. Job progress is the `admin.job_status` action.
- **Health**: `GET /healthz` on localhost only (D36) reports the gateway shards, the database pool, the queue backlog, the version and each plugin's status.

## Core admin plugin

Always enabled. Its management actions are slash only and need Manage Server by default, except where noted. Its config holds the server's log channel.

- `/plugins list | enable | disable | config`. `list` shows each installed plugin's state in this server: enabled, disabled or not loaded with its reason.
- `/audit channel` to set the log channel, and `/audit recent` for the last entries.
- `/tokens create | list | revoke` (D16). Any member can manage their own tokens. Other members' tokens need Manage Server.
- `READ` actions on all surfaces, which an LLM or a backend uses to find IDs and follow progress:
    - `describe_guild`: channels and roles the member can see, paged, from the cache. No extra permission.
    - `find_members`: up to 100 members whose name starts with the query, from Discord's gateway member search (D25). No extra permission.
    - `job_status`: progress of a job in this server. The member who started the job can always see it, and other jobs need Manage Server.

## Operator CLI

`uv run teabot` (D34). The subcommands work on the database alone.

- `init` sets up a new instance (D43): it creates the owner and runtime roles and the database if they are missing, writes generated secrets into the env file, and prints the Discord application settings and intents to use.
- `run` starts a bot process. systemd or Compose calls this.
- `new-plugin <id>` scaffolds a local plugin as a workspace member (D44, D45).
- `plugins list` shows installed and orphaned plugins, versions and migration versions, which plugins are loaded according to `pg_locks`, and the hosts and versions from `core.processes`.
- `plugins purge <id>` (D7, steps in the lifecycle section), which writes an audit row with the operator's system user.
- Blocking is a setting: add the plugin id to `TEABOT_DISABLED_PLUGINS` in the env file and restart (D13).

## Testing

- Tests run against a real Postgres. Each harness creates its own database from a migrated template, so tests never share rows.
- Core logic (registry, permissions, plans, queue, transactions, tokens) runs against the fakes in `teabot.testing` and against fake plugins passed straight to the loader. No Discord connection or packaging is needed. Using the same fakes the SDK ships keeps them honest.
- The migration runner has tests for ordering, rollback of a failed file, a dollar-quoted trigger, a table without `guild_id` being refused, two runners starting at once under the advisory lock, and refusal of a downgrade.
- Role isolation has tests, taken from spike S1, that a plugin role cannot read or reference `core` or another plugin's schema, that an injected stacked `; RESET ROLE` and an injected `set_config('role', ...)` are both refused, that `SET ROLE teabot_app` is refused even from a plpgsql function, and that the plugin's own `schedule_job` queues a job under its plugin id.
- The database helpers have tests that a plugin connection is back to `teabot_plugins` with no role set after release, that no helper runs two statements, and that calling `ctx.db` inside an open block raises.
- The loader has tests for a bad id, a reserved id, two packages with the same id, an entry point whose name is not the plugin's id, and each load-time schema check.
- The queue has tests for two workers never claiming the same job, shard filtering after a change of shard count, one running apply per guild, and reclaiming a job whose lease ran out.
- The lifecycles have tests for each row of the lifecycle table, purge refusing while a process holds the plugin's shared lock, an orphaned plugin picked up again on reinstall, and a guild re-added within the grace period or purged after it.
- The locks have tests that a second process with an overlapping shard refuses to start, and that it starts at once after the first process's lock connection is killed.
- The maintenance job has tests that it creates future `core.audit_reads` partitions and drops expired ones.
- Plan tests check that the diff hash is stable for equal state and changes when a target changes, and that a confirm value is refused for another actor, other input or an expired plan.
- The schema builder has snapshot tests from each supported field type to MCP JSON schema and to Discord command JSON, and the renderer has snapshot tests for default embeds.
- Each plugin keeps its own tests in `plugins/<id>/tests/`, including the stored config samples of D10 and its `snapshot.json`.
- End-to-end checks run by hand against a test server before a release.

## Deferred work

Each item is an addition to the design, with what triggers it.

| Item | Trigger | Shape |
|---|---|---|
| Hosted-service quotas (D47) | opening the hosted service | per-server limits on pending jobs, scheduled jobs, rows per plugin table and actions per minute, and queue claims that rotate between servers so a busy one cannot hold every worker |
| Plugin entitlements (D47) | paid tiers on the hosted service | an entitlement check in `/plugins enable`, fed by Discord's app subscriptions |
| Shard clusters (D40) | one process runs short of memory or CPU, or one host is not enough | several `teabot run --shards a-b` processes sharing Postgres, each holding its shard locks; step 1 of the call path forwards a request to the cluster that owns the guild's shard, found through `core.processes`; queue claims already filter by shard; rolling restarts, with the migration rule of D9 made required |
| Guild export and import | a server moving between instances, such as from a self-hosted instance to the hosted service | `teabot guilds export <id>` and `import`, carrying core rows and each plugin's rows by `guild_id` |
| Out-of-process plugins | hosted-service users who want custom code | plugins that run on the user's own server and receive actions over signed HTTP, so the hosted process never runs their code |
| Per-server permission overrides (D19) | event captains, or another role, need an action their Discord permissions do not give | a `core.permission_overrides` table, `/permissions show`, `set` and `reset`, one branch in step 5 of the call path |
| Discord event hooks (D6) | SDK 1.3 | `registrar.on(event, handler)` for joins, leaves, messages and reactions, run only for the servers that enable the plugin, each call isolated so one failing hook cannot stop another |
| Plugin dependencies (D6) | a plugin that builds on another | a `requires` manifest field, load and migration order through `graphlib`, failure passed on to dependents, `USAGE` grants on the required plugin's schema, and purge refusing while a dependent is installed |
| Buttons and select menus (D6, D32) | SDK 1.2, with giveaway entry as the first use | a component custom id `teabot:<plugin>:<action>:<payload>` routed to that action, so a press goes through the one call path with its permission check and audit (D1) |
| Autocomplete (D6) | SDK 1.2, for input fields whose choices are too many for a `Literal` | an optional autocomplete function per field, routed by the interaction router |
| Member data deletion (D11) | SDK 1.2, since giveaways keeps per-member data, and before the hosted service opens | `teabot users delete` and a `delete_user_data` plugin hook |
| A per-member store (D8) | several plugins writing the same small per-member tables | a Red-style scoped store with Pydantic models per scope |
| OAuth 2.1 (D16) | staff need claude.ai or the desktop app, whose custom connectors use OAuth, and before the hosted service opens | a second issuer of `core.tokens` rows, with Discord login and a consent screen that picks one server and a maximum risk |
| Egress in the manifest, with consent and enforcement (D12) | the hosted service | an `egress` manifest field, RuneLite-style consent at enable time, and an HTTP client that refuses undeclared hosts |
| Voice (D54) | a music plugin, after S8 | `ctx.voice` with `join`, `play`, `stop`, `leave` and `state`; `core.voice_sessions`; `on_track_end` and `on_voice_resume` jobs; a fake connection in `teabot.testing`; and a `voice` extra pinning `davey<0.2`, in a minor SDK release |
| Context-menu commands, MCP resources, HTTP routes (D6) | a plugin that needs one | new `Registrar` methods in a minor SDK release |

Notes for the OAuth work, checked against `mcp` 2.3.0 (pin `mcp>=2.3,<3`):

- `OAuthAuthorizationServerProvider` provides dynamic client registration, PKCE, refresh, revocation and the metadata routes. teabot implements the storage and the Discord step inside `authorize`.
- `AuthSettings.validate_token_resource` refuses tokens issued for another resource (RFC 8707). teabot sets it to `True`.
- The SDK has no server-side support for Client ID Metadata Documents. Dynamic registration covers current Claude clients.

## Prior art

Each of these solved a problem teabot has, and the decisions above borrow from them.

- **RuneLite Plugin Hub:** plugins as a repository and a reviewed, pinned commit; declared third-party connections; delisting as a kill switch; a broken plugin unloaded without stopping the client (D4, D5, D12, D13, D14).
- **Home Assistant:** an open-source core that anyone runs at home, with custom integrations an owner adds without anyone's approval, beside a reviewed set that ships with the core (D42, D44).
- **Red-DiscordBot:** a self-hosted, plugin-based Discord bot; `red_delete_data_for_user` for data deletion requests; the `Config` API as the model for a per-member store (D8, D11).
- **Debian `apt`:** `remove` keeps a package's data and `purge` deletes it (D7).
- **Terraform:** plan then apply, and refusing a saved plan once the state it was made from has changed (D21).
- **GitHub token format:** prefixed tokens that carry an id and are easy for secret scanners to find (D16).
- **Oban and Graphile Worker:** durable job queues in Postgres built on `SKIP LOCKED` claims and `LISTEN`/`NOTIFY` (D31).
- **Discord's sharding formula:** `(guild_id >> 22) % shard_count` decides which process owns a server (D40).

## Open decisions

1. **When OAuth lands (D16).** Claude Code works with a bearer token. An OAuth issuer is added once someone needs claude.ai or the desktop app.
2. **The plugin hub catalogue.** Its format, and how it reserves ids across listings (D46), can wait until a second plugin author exists.
