# trmnl-opencode

A [TRMNL](https://usetrmnl.com) plugin that shows usage statistics from an
[OpenCode](https://opencode.ai) server — sessions, token spend and cost,
bucketed hourly (last 24h), daily (last 7 days) and monthly (last 30 days),
plus a per-model breakdown.

## How it works

No backend to host. The plugin polls the OpenCode server's HTTP API directly
from the TRMNL plugin service — the same way the trmnl-mealie and trmnl-immich
plugins poll their source APIs. A small Python transform script computes the
usage buckets and shapes the data for the Liquid templates:

```
┌──────────┐  GET /api/session?limit=10000   ┌──────────────┐
│ TRMNL    │ ──────────────────────────────► │ OpenCode     │
│ service  │   Authorization: Bearer <pw>    │ web server   │
│  (polls) │ ◄────────────────────────────── │              │
└──────────┘        session list JSON        └──────────────┘
      │
      ▼
 src/transform.py   buckets sessions hourly / daily / monthly,
                    sums tokens and cost, groups by model
      │
      ▼
   Liquid templates render the card
```

The transform:

- reads the session list from the polled payload (`data` array) — subagent
  sessions are included, so the numbers reflect the full cost and token spend;
- buckets sessions by their `time.created` (epoch ms) in the configured
  timezone, so hourly and daily boundaries match your local clock;
- aggregates cost and tokens (input / output / reasoning / cache read /
  cache write) for today, last 7 days, last 30 days and all time;
- ranks models by spend for the card's model breakdown.

All computation is local and uses the Python standard library only — no
network calls in the transform, so it runs fine in the hosted TRMNL sandbox.

The `trmnl/` directory is a [trmnlp](https://github.com/owise1/trmnlp) plugin
project (Liquid templates + settings + transform) pushed to your TRMNL plugin
via `trmnlp push`.

## TRMNL plugin setup

1. Create a new plugin in the TRMNL dashboard (or push via `trmnlp push`).
2. Set the custom fields:
   - **url** — your OpenCode server address, e.g. `https://opencode.vandijke.xyz`
     (or `http://<host>:4096` when running `opencode web`).
   - **api_key** — only needed if the server runs with a password
     (`OPENCODE_SERVER_PASSWORD`); sent as `Authorization: Bearer` on each poll.
   - **timezone** — the IANA timezone the server clock runs in, e.g.
     `Europe/Amsterdam`. Used to bucket sessions by local hour and day.
3. Set the refresh interval to 60 minutes (or your preferred cadence).

## Development

The transform is plain Python and needs no dependencies. Validate it against
a sample polled payload:

```sh
python3 src/transform.py < fixture.json
```

For a live preview, run `trmnlp serve` inside `trmnl/`.

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
