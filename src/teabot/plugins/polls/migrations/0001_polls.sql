-- Polls (polls.md): Discord's own polls the plugin started, and their results once read. No member's vote is kept.
CREATE TABLE polls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    message_id bigint NOT NULL,
    question text NOT NULL,
    answers text[] NOT NULL,
    multiple boolean NOT NULL,
    -- When Discord ends it, from the message it returned, and when it was ended early, if it was.
    ends_at timestamptz NOT NULL,
    ended_at timestamptz,
    -- running until its results are read; gone when its message was deleted first; unread when the bot lost sight
    -- of its channel for a day.
    state text NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'ended', 'gone', 'unread')),
    -- Each answer's votes, in the order of answers, once read; null when Discord sent no counts.
    votes int[],
    final boolean,
    started_by teabot.member_actor,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (guild_id, id)
);
CREATE INDEX polls_running ON polls (guild_id, ends_at) WHERE state = 'running';
