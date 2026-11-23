# Announcements

Post an announcement to a channel as an embed, now or later. Turn it on with `/plugins enable plugin:announcements`.

## Post an announcement

```text
/announcements post channel:#news title:Server event this Saturday colour:gold
```

A form opens for the announcement's text, which can span several lines. Send it, and teabot posts the embed and replies with a link to it.

| Option | What it is |
|---|---|
| `channel` | The channel to post in. |
| `title` | The announcement's title, up to 256 characters. |
| `colour` | `blurple`, `green`, `red` or `gold`. `blurple` if left out. |

Announcements mention nobody. An `@everyone` in the text stays plain text.

## Post one later

```text
/announcements schedule channel:#news title:Event starts now in_minutes:60
```

The same as `post`, with `in_minutes`: how many minutes from now, up to a week. teabot replies with the time, and a job number to follow it with `/admin job_status`. If the channel is gone by then, or the bot may no longer post there, the job fails and says why.

## Who can use it

| Command | Needs |
|---|---|
| `/announcements post` | Manage Messages |
| `/announcements schedule` | Manage Messages |

To let a role post announcements, give it Manage Messages. See [Who can use what](../permissions.md).

## Settings

None.

## For AI assistants

The tools are `announcements_post` and `announcements_schedule`.

**Next:** [Polls](polls.md).
