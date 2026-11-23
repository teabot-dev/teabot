# Turn on plugins

At the end of this page, the plugins you want are on in your server, with the settings you want.

A **plugin** adds a set of commands to teabot, such as `/giveaways`. Announcements and roles come with teabot, and the operator can install more from the [plugin hub](../hub/index.md). Every plugin is off in your server until you turn it on. These commands need **Manage Server**.

## See the plugins

```text
/plugins list
```

teabot lists each plugin, and whether it is on here:

```text
announcements enabled
giveaways disabled
roles disabled
```

A plugin may say `not loaded`, with a reason. That is for the operator to fix, and the plugin cannot be turned on until they do.

## Turn one on

```text
/plugins enable plugin:giveaways
```

The `plugin` option suggests the plugins that are off here as you type. teabot replies `giveaways is now enabled here.`, and the plugin's commands appear under `/giveaways`. Discord can take a minute to show new commands. If they do not appear, reload Discord with ++ctrl+r++, or ++cmd+r++ on macOS.

## Turn one off

```text
/plugins disable plugin:giveaways
```

Its commands disappear from your server. Its data and settings are kept, so turning it on again carries on where it left off. While it is off, its scheduled work, such as a giveaway's draw or a scheduled announcement, waits, and runs when you turn it back on.

## Change a plugin's settings

See a plugin's settings:

```text
/plugins config plugin:giveaways
```

```json
{
  "default_minutes": 60,
  "excluded_role": null
}
```

Change one. The `plugin` and `setting` options suggest names as you type:

```text
/plugins config plugin:giveaways setting:default_minutes value:90
```

- Type numbers, and `true` or `false`, as they are. To clear a setting, type `null`.
- For a channel, role or member, type `#` or `@` and pick it from Discord's list. Its ID works too: to copy one, turn on **Developer Mode** in Discord's **Advanced** settings, then right-click the channel, role or member and choose **Copy ID**.
- teabot checks the value, and says what is wrong if it does not fit.

Each plugin lists its settings: [Announcements](plugins/announcements.md), [Polls](plugins/polls.md), [Roles](plugins/roles.md) and [Tickets](plugins/tickets.md) here, and a plugin from the hub in the README its hub listing links to.

Admins can do the same in a browser, on [your teabot's site](site.md).

**Next:** [Your teabot's site](site.md).
