CREATE TABLE ballots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    question text NOT NULL,
    options text[] NOT NULL,
    ends_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'closed')),
    UNIQUE (guild_id, id)
);

CREATE TABLE votes (
    guild_id bigint NOT NULL,
    ballot_id bigint NOT NULL,
    member_id teabot.member_id NOT NULL,
    choice int NOT NULL,
    PRIMARY KEY (guild_id, ballot_id, member_id),
    FOREIGN KEY (guild_id, ballot_id) REFERENCES ballots (guild_id, id) ON DELETE CASCADE
);
