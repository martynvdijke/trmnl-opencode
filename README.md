# trmnl-opencode

TRMNL plugins for [OpenCode](https://opencode.ai) — two plugins in this repo:

- **OpenCode Usage** (`trmnl/usage/`) — usage statistics from your own OpenCode
  server: sessions, token spend and cost, bucketed hourly (last 24h), daily
  (last 7 days) and monthly (last 30 days), plus a per-model breakdown.
- **OpenCode Go Limits** (`trmnl/limits/`) — how much of your
  [OpenCode Go](https://opencode.ai/go) subscription allowance is used,
  for the rolling / weekly / monthly limit windows, with when each resets.

## How it works

No backend to host. The plugins poll their APIs directly from the TRMNL plugin
service — the same way the trmnl-mealie and trmnl-immich plugins poll their
source APIs. A small Python transform script computes the data and shapes it
for the Liquid templates:

```
┌──────────┐  GET /api/session?limit=10000   ┌──────────────┐
│ TRMNL    │ ──────────────────────────────► │ OpenCode     │
│ service  │   Authorization: Bearer <pw>    │ web server   │
│  (polls) │ ◄────────────────────────────── │              │
└──────────┘        session list JSON        └──────────────┘
      │
      ▼
 usage/src/transform.py   buckets sessions hourly / daily / monthly,
                          sums tokens and cost, groups by model
      │
      ▼
   Liquid templates render the card
```

```
┌──────────┐  GET https://opencode.ai/zen/go/v1/usage
│ TRMNL    │ ──────────────────────────────────────► ┌─────────────┐
│ service  │   Authorization: Bearer <Go API key>    │ OpenCode Go │
│  (polls) │ ◄────────────────────────────────────── │ gateway     │
└──────────┘          usage limits JSON              └─────────────┘
      │
      ▼
 limits/src/transform.py   parses rolling / weekly / monthly limits,
                           computes resets, picks status colors
      │
      ▼
   Liquid templates render the card
```

The transforms:

- **usage** — reads the session list from the polled payload (`data` array) —
  subagent sessions are included, so the numbers reflect the full cost and
  token spend; buckets sessions by their `time.created` (epoch ms) in the
  configured timezone; aggregates cost and tokens (input / output / reasoning /
  cache read / cache write) for today, last 7 days, last 30 days and all time;
  ranks models by spend for the card's model breakdown.
- **limits** — reads the OpenCode Go usage payload (`usage.rolling`,
  `usage.weekly`, `usage.monthly`), each reporting how much of the allowance is
  consumed (`percent`) and when it resets (`resetsAt`); renders a relative
  countdown plus the local reset time in the configured timezone, colored by
  how close the limit is (green / amber / red).

All computation is local and uses the Python standard library only — no
network calls in the transforms, so they run fine in the hosted TRMNL sandbox.

Each `trmnl/<plugin>/` directory is a
[trmnlp](https://github.com/owise1/trmnlp) plugin project (Liquid templates +
settings + transform) pushed to your TRMNL plugin via `trmnlp push`.

## TRMNL plugin setup

1. Create the plugins in the TRMNL dashboard (or push via `trmnlp push` — the
   release workflow auto-creates a plugin the first time when no ID is set).
   After a plugin is created, copy its numeric ID into its settings file:
   `trmnl/usage/src/settings.yml` and `trmnl/limits/src/settings.yml`
   (`id: <plugin-id>`). This makes later pushes update the same plugin instead
   of creating another one.
2. Set the custom fields:
   - **usage**: **url** — your OpenCode server address, e.g.
     `https://opencode.vandijke.xyz` (or `http://<host>:4096` when running
     `opencode web`). **api_key** — only needed if the server runs with a
     password (`OPENCODE_SERVER_PASSWORD`); sent as `Authorization: Bearer` on
     each poll. **timezone** — the IANA timezone the server clock runs in,
     e.g. `Europe/Amsterdam`.
   - **limits**: **api_key** — your OpenCode Go API key from
     [opencode.ai/auth](https://opencode.ai/auth); sent as
     `Authorization: Bearer` on each poll. **timezone** — used to show the
     reset times in local time, e.g. `Europe/Amsterdam`.
3. Set the refresh interval (60 minutes, or your preferred cadence).

## Development

The transform is plain Python and needs no dependencies. Validate it against
a sample polled payload:

```sh
python3 trmnl/usage/src/transform.py < tests/fixture.json
python3 trmnl/limits/src/transform.py < tests/fixture_limits.json
```

For a live preview, run `trmnlp serve` inside `trmnl/usage/` (or `trmnl/limits/`).

## Security notes

- The OpenCode server address is stored in your TRMNL plugin settings. If the
  server is exposed to the internet, set `OPENCODE_SERVER_PASSWORD` and fill
  the plugin's **api_key** field so only authorized polls succeed.
- The OpenCode API reports aggregate session metadata (titles, model names,
  token counts and cost) — no conversation content is polled.
- Transform scripts run automatically against the polled response — review
  any third-party plugin's `src/transform.*` before serving it, or set
  `transform_runtime: disabled` in `.trmnlp.yml`.

## License

See [LICENSE](LICENSE).
