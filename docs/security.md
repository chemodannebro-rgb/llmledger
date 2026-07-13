# Security model

`llm-burnwatch`'s zero-dependency core reads and writes local files and
prints to stdout/stderr. None of `report`/`demo-data`/`schema`/`status`/`validate`/
`dashboard`/`detect`/`train`/`budget` make a network call — enforced by
`test_core_commands_make_no_network_attempts` (`tests/test_cli.py`), which
patches `socket.socket` to raise if any of them tries to open one.

This page is a readable version of [`SECURITY.md`](https://github.com/chemodannebro-rgb/llm-burnwatch/blob/main/SECURITY.md)
and `ARCHITECTURE.md`'s "Network boundaries" section — the full trust
model in one place, not a new guarantee.

## Network/process boundaries at a glance

| Command | Boundary | Off by default? |
|---|---|---|
| `pricing import <url>` | Fetches a pricing JSON file over `http(s)://` | Yes — only with an explicit URL argument |
| `detect --follow --webhook-url`/`--slack-webhook-url` | POSTs each newly triggered alert as JSON (or a Slack-compatible payload) to a URL you supply | Yes — only with the flag/env var, and only in `--follow` mode |
| `detect --follow --telegram-bot-token`+`--telegram-chat-id` | POSTs each alert to the Telegram Bot API (`api.telegram.org`, host fixed by the sink) | Yes — same conditions as webhook/Slack |
| `detect --follow --exec-sink <command...>` | Runs a **local command** (not a network call) for each alert | Yes — only with the flag |

Nothing above ever runs implicitly, and one-shot `detect` never touches any
of them. `detect --follow` itself is excluded from the no-network test
(it's a long-running poll loop); the equivalent guarantee for it — that no
socket opens and no process spawns when no sink flags are given — is
checked separately, patching both `socket.socket` and `subprocess.Popen`.

## `pricing import <url>` trust boundary

`pricing import <source>` is the one explicit, opt-in exception to the
no-network-calls guarantee. It never runs implicitly — only when you
invoke this exact subcommand with a URL.

**What it does:** fetches `<source>` over `http(s)://` (10 second timeout,
10 MB response cap, rejects any other URL scheme such as `file://`, and
refuses to follow a redirect that downgrades an `https://` source to plain
`http://`), parses it strictly as JSON (rejecting `Infinity`/`NaN`/
non-object payloads), extracts only numeric cost-per-token fields, and
writes the result to `~/.config/llm-burnwatch/pricing.json`. The fetched
content is never executed — it's read as data, the same way `report`/
`dashboard`/`detect` already read the packaged `pricing.json`.

**What you're trusting:** the content at the URL you supply. A malicious
or compromised URL could supply inflated or deflated per-model rates,
silently skewing every future cost calculation until you re-import a
correct file. This is a data (pricing accuracy) risk, not a
code-execution risk.

## `detect --follow` state-file trust boundary

`--follow` persists progress (byte offset already consumed, plus the
current rolling window) to `<log-file>.llm-burnwatch-follow-state.json`,
written atomically (`tempfile.mkstemp` + `os.replace`) next to the log — a
process killed mid-write never leaves a half-written state file behind.

At load time, the file's top-level shape is validated before its contents
are trusted; a state file that's missing, unreadable, not valid JSON, or
the wrong shape is never fatal — `--follow` warns and starts over from the
beginning of the log rather than crashing.

**What this does not protect against:** the state file carries no
integrity check analogous to the model registry's sha256 below. Someone
with write access to it could hand-edit the byte offset (causing
`--follow` to skip or re-read parts of the log) or inject arbitrary JSON
into the persisted window. This is the same trust level as the log file
itself — if you don't already trust everyone with write access to
`--log-file`'s directory, the follow-state file carries no stronger
guarantee.

## `detect --follow` alert sinks trust boundary

Alert sinks (`WebhookSink`/`SlackSink`/`TelegramSink`/`ExecSink`) push each
newly triggered alert to a destination *you* configure. None run
implicitly, none run for one-shot `detect`, and a failure in one sink is
caught, reported via `warn()`, and never stops the poll loop or the other
configured sinks.

**Webhook/Slack**: an HTTP(S) POST using the same `urllib.request` +
10-second-timeout discipline as `pricing import`, including rejecting any
non-`http(s)://` scheme before any connection is attempted. Prefer the
`LLM_BURNWATCH_WEBHOOK_URL`/`LLM_BURNWATCH_SLACK_WEBHOOK_URL` environment
variables over the CLI flags for a URL that embeds a secret token, since
command-line arguments are visible to other local users via `ps`.

**Telegram** composes `WebhookSink` internally — a fixed
`https://api.telegram.org/bot<token>/sendMessage` endpoint (the host is
hard-coded, not caller-supplied). Prefer
`LLM_BURNWATCH_TELEGRAM_BOT_TOKEN`/`LLM_BURNWATCH_TELEGRAM_CHAT_ID` over the
CLI flags for the same `ps`-visibility reason.

**Exec sink** is the riskiest of the four: it runs a *local command you
specify*, writing the alert JSON to its **stdin** (never argv, since argv
is visible via `ps`/`/proc/<pid>/cmdline`). `shell=False` is hard-coded in
`ExecSink.send()` — not a parameter you or a future caller can override —
so the alert JSON is never concatenated into a string a shell re-parses.

**What this does not protect against:** `shell=False` only guarantees this
process doesn't hand your command line to a shell. If the command you
configure itself interprets its stdin as code/templates (e.g. `sh` reading
a script from stdin, a script that does its own `eval`/templating), that
command can still execute content derived from the alert. Point
`--exec-sink` only at a command that treats its stdin as an opaque string.

## Model registry trust boundary

`llm-burnwatch train` (the `[anomaly]` extra) writes a versioned model
registry under `models/vN/`: a `model.skops` file plus a `metadata.json`
recording a sha256 hash of `model.skops`. `detect` reads this registry back
for its ML cross-check.

**What this protects against:**

- **Corruption or accidental substitution** — `load_model()` recomputes
  the sha256 of `model.skops` and refuses to load if it doesn't match
  `metadata.json`.
- **Arbitrary code execution via deserialization** — models are
  serialized with `skops.io`, not `pickle`. Unlike `pickle`, `skops`
  refuses by construction to build any type outside an explicit trusted
  list, so a tampered file is rejected at load time (`load_model()` also
  checks `skops.io.get_untrusted_types()` before deserializing).

**What this does not protect against:** a coordinated substitution by the
same author/commit. Whoever controls the repository (or the CI job that
runs `train`) can replace `model.skops` with a different model and simply
recompute the sha256 to match. The integrity check only detects a mismatch
between the two files — it can't tell a legitimate `train` run from a
malicious one by the same party. This is a root-of-trust limitation, not a
bug: which party you trust to touch the registry is a process concern
(e.g. code review on the diff), not something a checksum can resolve.
`load_model()` prints a warning to this effect every time it loads a
model.

## Reporting a vulnerability

`llm-burnwatch` is an early-stage project — there is no SLA, but reports
are still welcome. Open a GitHub issue describing the problem; there is no
dedicated security contact or private disclosure channel for this project,
so treat any issue you open as public from the start.
