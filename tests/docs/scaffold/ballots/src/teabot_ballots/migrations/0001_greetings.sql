CREATE TABLE greetings (
    guild_id bigint NOT NULL,
    member_id teabot.member_id NOT NULL,
    greeting text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
