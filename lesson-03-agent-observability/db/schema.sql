-- Conversations of the support agent. SQLite, so it runs anywhere without a server.
-- The same tables work in Postgres (Supabase) with small changes: TEXT -> timestamptz for times, REAL -> numeric.

create table if not exists users (
  id          text primary key,           -- the customer id from the website, not a name or a phone number
  segment     text,                       -- e.g. private, fleet
  created_at  text not null
);

create table if not exists conversations (
  id          text primary key,           -- the same id is the Langfuse session_id
  user_id     text not null references users(id),
  channel     text not null default 'web',
  release     text,                       -- which version of the agent answered
  started_at  text not null,
  last_at     text not null,
  turns       integer not null default 0
);

create table if not exists messages (
  id              integer primary key autoincrement,
  conversation_id text not null references conversations(id),
  turn            integer not null,
  role            text not null check (role in ('user', 'assistant')),
  content         text not null,
  trace_id        text,                   -- the Langfuse trace of this turn: the link between the two systems
  created_at      text not null
);

create table if not exists steps (
  id              integer primary key autoincrement,
  conversation_id text not null references conversations(id),
  turn            integer not null,
  trace_id        text,
  seq             integer not null,       -- order inside the turn
  kind            text not null check (kind in ('llm', 'tool')),
  name            text not null,
  started_at      text not null,
  duration_ms     real not null,
  status          text not null default 'ok',
  error           text,
  input_tokens    integer not null default 0,
  output_tokens   integer not null default 0,
  cache_read_tokens integer not null default 0,
  cost_usd        real not null default 0,
  args_hash       text
);

create table if not exists feedback (
  conversation_id text not null references conversations(id),
  turn            integer not null,
  trace_id        text,
  name            text not null,          -- user_feedback, has_source, ...
  value           real not null,          -- 1 / 0 for yes / no
  comment         text,
  created_at      text not null
);

create index if not exists messages_conv_idx on messages(conversation_id, turn);
create index if not exists steps_conv_idx on steps(conversation_id, turn);
create index if not exists steps_name_idx on steps(name);
create index if not exists feedback_conv_idx on feedback(conversation_id);
