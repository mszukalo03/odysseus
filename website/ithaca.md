# Ithaca

Ithaca is the dashboard-homepage extension — a fullscreen screen of tiles.
**Weather** is the only tile that ships built in; every other tile is one you
build yourself, backed by a live read-only query against a Postgres database
you connect.

> Looking for the extension system itself (enabling/disabling, installing
> from a URL)? See [`extensions.md`](extensions.md). This page is about using
> Ithaca specifically, once it's enabled.

## The short version

- Open Ithaca from the sidebar, the temple-rail button, or the `/ithaca`
  deep link.
- **Weather** needs an OpenWeatherMap API key (Settings → Integrations →
  "Ithaca Hub").
- Everything else starts with a **Database Connection** (Settings →
  Integrations → "Database Connections") — a read-only Postgres connection.
  Once you have one, you can build tiles against it by hand, by describing
  what you want in plain English, or by importing one someone else exported.
- Every tile is **drag-to-move** and **drag-corner-to-resize**. Admins only.
- Custom tiles are automatically answerable from chat — ask "what version of
  X is running" and the agent will find and query the right tile.

## Setting up Weather

Settings → Integrations → **Ithaca Hub** card:

| Field | What it is |
|---|---|
| OpenWeatherMap key | Free tier key from [openweathermap.org](https://openweathermap.org/api) |
| Home coordinates | Latitude/longitude of the location you want forecasts for |
| Units | Metric (°C) or Imperial (°F) |

These can also be set via `OPENWEATHER_API_KEY`/`OPENWEATHER_LAT`/
`OPENWEATHER_LON`/`OPENWEATHER_UNITS` in `.env` — a value saved in Settings
takes priority over the env var when both are set.

## Connecting a database

Settings → Integrations → **Database Connections** card → **Add
Connection**. This is a plain Postgres connection (host, port, database,
username, password, SSL mode) — nothing Ithaca-specific about the database
itself. The password is encrypted at rest (the same mechanism used for email
account passwords).

**Use a dedicated read-only role, not an existing write-capable one.**
Queries run through this connection are enforced read-only at two
independent layers (Odysseus rejects anything but a single `SELECT`, and
issues `SET TRANSACTION READ ONLY` so Postgres itself blocks writes even if
something slipped past the first check) — but the real safety boundary is
least-privilege credentials:

```sql
CREATE ROLE ithaca_reader LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE main_db TO ithaca_reader;
GRANT USAGE ON SCHEMA public TO ithaca_reader;
GRANT SELECT ON app_config TO ithaca_reader;  -- or ALL TABLES for the whole schema
```

Click **Test Connection** (the dot next to the connection in the list turns
green/red) before building tiles against it.

One connection can back as many tiles as you want, across as many different
tables as you want — connecting a database doesn't commit you to any
particular table or query shape.

## Building a tile

Three ways, all reachable from the **Add Tile** / **Import Tile** buttons in
Ithaca's header (admin-only):

### By hand

Fill in the connection, a title, a SQL query (a single `SELECT`), and a viz
type (table, stat, bar, line, pie, or list). For bar/line/pie/stat, set which
columns are the label and the value. Preview before saving.

### AI-generated

Pick a connection, optionally paste some free-form notes about the data
(schema docs, quirks, anything that helps), and describe what you want in
plain English — e.g. *"show me apps flagged for review, with their deployed
and latest versions"* or *"count apps by update status as a bar chart"*. The
tile builder:

1. Reads the connection's live schema (table/column names and types) —
   nothing is guessed or hardcoded.
2. Sends one request to whichever model you've set as your Utility (or
   Default) model in Settings → AI Defaults.
3. Validates the response, auto-repairs the one most common mistake small
   models make (an unquoted multi-word column alias), and **runs the query
   before showing it to you** — a bad query surfaces as a clear error
   instead of a silently broken tile.
4. Shows you the generated title/query/viz type, editable, with a Preview
   button before you save.

This works with small local models — it's deliberately built to ask for the
minimum output a model has to get right (title, query, viz type, and which
columns are the chart axes; nothing else), rather than a large nested JSON
structure. If the model doesn't finish quickly, check Settings → AI Defaults
that your Utility/Default model is actually a fast one — a "thinking" model
that reasons at length before answering will time out here.

### Imported

Paste the JSON contents of a `.tile.json` package (downloaded from another
Odysseus instance, or from this one) and pick which of *this* instance's own
connections to bind it to. An imported package never carries credentials —
only a hint about what kind of connection it expects (e.g. "a postgres
connection like `homelab_main_db`"). You always explicitly choose the local
connection; it's never auto-matched by name.

## Action buttons

A tile isn't limited to displaying data — it can also carry one or more
buttons that hit an HTTP endpoint you configure, e.g. an n8n webhook that
kicks off a reprocessing job for the data the tile shows.

**1. Add a webhook endpoint** — Settings → Integrations → **Webhook
Endpoints**: give it a label, the URL, the HTTP method (GET/POST/PUT/PATCH),
and optional auth (none, a bearer token, or basic `user:pass`). The URL and
token stay on this instance; a tile only ever references the endpoint by
name.

**2. Add an action to a tile** — in the tile builder, under "Action buttons",
pick a label and an endpoint, then **+ Add Action**. Add as many as you like
before saving the tile.

**3. Click it** — the button appears in the tile's footer. By default it
asks for confirmation first (a webhook is a real side effect, not a data
fetch); the confirmation text is customizable. On click, the server fires
the request (with its configured auth) and the button briefly shows
Running…/Done/Failed.

Clicking a button needs the same access level as viewing the tile itself —
admin-only is only required to *create or edit* an endpoint or an action,
matching the same split as viewing vs. editing tile data.

Only `http://`/`https://` URLs and `GET/POST/PUT/PATCH` are allowed — no
other schemes or verbs. There's no read-only enforcement here the way there
is for tile queries (a webhook's whole point is to trigger something); the
safety boundary is which URL you were willing to configure.

## Arranging tiles

Every tile — Weather included — can be **dragged by its header** to move it,
and **dragged from its bottom-right corner** to resize it. Changes save
automatically as you let go. This is admin-only; other users see the layout
as last arranged.

## Deleting a tile

The trash-can icon in a tile's header (admin-only, next to Refresh).
Confirms before deleting. Weather can't be deleted — it's the one built-in
tile — but every other tile can be.

## Exporting / importing tiles

The download icon in a tile's header downloads a `.tile.json` file — the
tile's config plus a non-secret hint about what connection (and any action
endpoints) it expects, never credentials. Hand that file to another
Odysseus instance's **Import Tile** flow to recreate it there, bound to
that instance's own connection — and, if the tile has action buttons, its
own webhook endpoint(s) too. Every action must be explicitly bound; none of
it is auto-matched by name.

## Using tiles from chat

Every custom tile is automatically queryable by the AI assistant — you don't
need to do anything extra to enable this. Ask things like:

- "What version of Radarr is deployed?"
- "Which apps are flagged for review?"
- "What's the breakdown of update status across my apps?"

The assistant first lists your tiles (if it doesn't already know which one
covers your question), then runs the matching one and reads its live data
back to you — the same query, same data, same cache as what's on screen. It
never writes or runs arbitrary SQL of its own; it only ever runs a tile
you've already saved.

## Troubleshooting

**"OPENWEATHER_API_KEY is not configured"** — set the key in Settings →
Integrations → Ithaca Hub, or the env var.

**A tile shows a query error** — click into the tile builder, fix the SQL in
the query field, and Preview again. The error message is the raw Postgres
error, so it'll usually say exactly what's wrong (unknown column, syntax
error, etc.).

**AI generation is slow or returns nothing** — your configured Utility/
Default model may be a "thinking"/reasoning model spending its whole token
budget on hidden reasoning instead of answering directly. Point Settings →
AI Defaults → Utility Model at a smaller, faster model, or one better suited
to quick structured-output tasks.

**A connection's Test button is red** — check host/port/credentials, and
that the database accepts connections from wherever Odysseus is running
(firewall, `pg_hba.conf`, Docker networking if Postgres is in another
container).
