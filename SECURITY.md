# Security policy

`llm-burnwatch` is an early-stage project — there is no SLA, but reports are
still welcome.

## Reporting a vulnerability

Please open a GitHub issue on this repository describing the problem. There
is no dedicated security contact or private disclosure channel for this
project; treat any issue you open as public from the start.

## Model registry trust boundary

`llm-burnwatch train` (the `[anomaly]` extra) writes a versioned model registry
under `models/vN/`: a `model.skops` file plus a `metadata.json` recording,
among other things, a sha256 hash of `model.skops`. `llm-burnwatch detect` reads
this registry back for its ML cross-check.

What this protects against:

- **Corruption or accidental substitution.** `load_model()`
  (`src/llm_burnwatch/anomaly/registry.py`) recomputes the sha256 of
  `model.skops` and refuses to load if it doesn't match `metadata.json`.
- **Arbitrary code execution via deserialization.** Models are serialized
  with `skops.io`, not `pickle`. Unlike `pickle`, `skops` refuses by
  construction to construct any type outside an explicit trusted list, so a
  tampered or unexpected file is rejected at load time (`load_model()` also
  checks `skops.io.get_untrusted_types()` before deserializing).

What this does **not** protect against:

- **A coordinated substitution by the same author/commit.** If whoever
  controls the repository (or the CI job that runs `llm-burnwatch train`)
  replaces `model.skops` with a different model trained on different data,
  they can simply recompute the sha256 and write the new, matching value
  into `metadata.json` at the same time. The integrity check only detects
  a mismatch between the two files — it cannot tell a legitimate
  `llm-burnwatch train` run from a malicious one by the same party, because
  both produce an internally consistent pair of files.

This is a root-of-trust limitation, not a bug: no purely local, code-level
check can distinguish "the maintainer re-trained the model" from "the
maintainer (or anyone with commit/CI access) swapped in a different model"
— that distinction is a question of *who* you trust to touch the registry,
which is a process concern (e.g. code review on the diff introducing a new
`models/vN/` directory before merging), not something a checksum can
resolve. `load_model()` prints a warning to this effect every time it loads
a model, as a reminder to only load registries from a source you trust.

See also [Network/process boundaries at a glance](docs/security.md#networkprocess-boundaries-at-a-glance)
for `llm-burnwatch`'s no-network-calls guarantee.

## `pricing import <url>` network trust boundary

`llm-burnwatch pricing import <source>` is the one explicit, opt-in exception
to the no-network-calls guarantee above (see "Network boundaries" in
`ARCHITECTURE.md`). It never runs implicitly — only when you invoke this
exact subcommand with a URL.

What it does: fetches `<source>` over `http(s)://` (a 10 second timeout, a
10 MB response cap, rejection of any other URL scheme such as `file://`,
and refusal to follow a redirect that downgrades an `https://` source to a
plain `http://` response), parses it strictly as JSON (rejecting `Infinity`/`NaN`/non-object payloads),
extracts only numeric cost-per-token fields, and writes the result to
`~/.config/llm-burnwatch/pricing.json`. The fetched content is never executed —
it is read as data (numbers keyed by model name), the same way `report`/
`dashboard`/`detect` already read the packaged `pricing.json`.

What you're trusting when you run it: the content at the URL you supply.
Only import from a source you trust — a malicious or compromised URL could
supply inflated or deflated per-model rates, which would silently skew every
future `report`/`dashboard`/`detect` cost calculation until you re-import a
correct file or delete `~/.config/llm-burnwatch/pricing.json`. This is a data
(pricing accuracy) risk, not a code-execution risk.

## `detect --follow` state-file trust boundary

`detect --follow` persists its progress (the byte offset already consumed
from `--log-file`, and the current rolling analysis window) to
`<log-file>.llm-burnwatch-follow-state.json`, a plain JSON file written next
to the log with the same atomic-write pattern (`tempfile.mkstemp` +
`os.replace`) already used by `pricing import`.

What this protects against: a process killed mid-write never leaves a
half-written state file behind (the temp file is renamed into place only
after the write completes). At load time, the file's top-level shape is
validated (`offsets` must be an object, `window` a list) before its contents
are trusted; a state file that's missing, unreadable, not valid JSON, or the
wrong shape is never fatal — `--follow` warns and starts over from the
beginning of the log rather than crashing or silently misbehaving.

What this does **not** protect against: the state file is read back as data
(byte offsets and a list of previously seen log records) with no integrity
check analogous to the model registry's sha256 above. Someone with write
access to this file could hand-edit it to change the byte offset `--follow`
resumes from (causing it to skip or re-read parts of the log) or inject
arbitrary JSON objects into the persisted `window`, which would then be
re-analyzed by the detector registry on the next poll alongside genuine log
records. This is the same trust level as the log file itself: if you don't
trust everyone with write access to `--log-file`'s directory, you're
already trusting them not to tamper with the log, and the follow-state file
sitting alongside it carries no stronger guarantee.

## `detect --follow` alert sinks trust boundary

`detect --follow --webhook-url <url>` / `--slack-webhook-url <url>` /
`--telegram-bot-token <token> --telegram-chat-id <id>` /
`--exec-sink <command...>` (`src/llm_burnwatch/sinks/`) push each newly
triggered alert to a destination *you* configure. None of them run
implicitly, none run for one-shot `detect` (only `--follow`), and a failure
in one sink (`sinks.protocol.send_to_all`) is caught, reported via `warn()`,
and never stops the poll loop or the other configured sinks.

**`WebhookSink`** (`webhook_sink.py`): an HTTP(S) POST of the alert's full
`dataclasses.asdict(alert)` JSON (unaffected by B4 below -- this sink is for
machine consumers, not chat) to the URL you supply, using the same
`urllib.request` + fixed 10s timeout discipline as `pricing import <url>`,
including the same rejection of any non-`http(s)://` URL scheme (`file://`,
etc.) at construction time, before any connection is attempted. The response
body is never read (only `response.status` is inspected), so unlike
`pricing import` there's no response-size cap to enforce -- nothing from the
response is ever buffered. What you're trusting when you set this: the URL
itself -- llm-burnwatch will send it every alert's full `evidence`/`message`
payload, which can include `label`/model/cost data from your own log. Prefer
the `LLM_BURNWATCH_WEBHOOK_URL` environment variable over the
`--webhook-url` flag for a URL that embeds a secret token, since
command-line arguments are visible to other local users via `ps`.

**`SlackSink`/`TelegramSink`** (`slack_sink.py`/`telegram_sink.py`) both
compose `WebhookSink` internally for the HTTP POST/error handling, but post
a single human-readable line (`alert_text.format_alert_oneline` -- severity
as an emoji, a plain-language incident type, a money-first detail, and the
record number, e.g. `"🚨 llm-burnwatch: rule violated: call cost limit
exceeded -- call cost exceeded (record #3)"`) as a `{"text": ...}` payload,
not the full `evidence` dict `WebhookSink` sends -- a chat message is meant
to be read by a person glancing at a phone, not parsed by a machine. This
line is plain text, never Markdown/HTML `parse_mode`, so there's no
message-escaping logic that could be gotten wrong (the emoji are literal
UTF-8 characters, not markup, so they don't reintroduce that risk).
`TelegramSink` is not a third independent HTTP implementation: it is a
fixed `https://api.telegram.org/bot<token>/sendMessage` endpoint (the host
is hard-coded by the sink, not caller-supplied, so there is no arbitrary
URL/scheme to validate here). What you're trusting when you set either of
these: the destination itself (a Slack incoming-webhook URL, or a Telegram
bot token embedded the same way) -- a `SinkError` from a failed delivery
includes the URL, so that error is only ever passed to local `warn()`,
never sent anywhere else. Prefer the
`LLM_BURNWATCH_SLACK_WEBHOOK_URL`/`LLM_BURNWATCH_TELEGRAM_BOT_TOKEN`/
`LLM_BURNWATCH_TELEGRAM_CHAT_ID` environment variables over
`--slack-webhook-url`/`--telegram-bot-token`/`--telegram-chat-id` for the
same `ps`-visibility reason as `--webhook-url` above.

**`ExecSink`** (`exec_sink.py`) is the riskiest of the four: it runs a
*local command you specify*, writing the alert JSON to its **stdin**. This
is the sharpest trust boundary in this section, not a variant of the
webhook risk above -- the concern isn't "data goes to a URL you chose",
it's "a command runs locally with attacker-influenceable content in its
input" (a log record's `label`/`extra` fields, which end up in `evidence`,
are not necessarily written by you).

The alert is deliberately passed via stdin rather than as an argv entry:
`command` (the fixed argv you configured) never changes, but the alert
JSON does, once per delivery, and process argv is visible to every other
local user via `ps`/`/proc/<pid>/cmdline` -- stdin is not. This is the same
"don't put a secret/variable payload where `ps` can see it" discipline
applied to `--webhook-url`/`--slack-webhook-url` above, just for the
payload instead of the destination.

What protects against shell injection: `command` is always a list of argv
strings (never a single shell string you type), and it is passed to
`subprocess.run(..., shell=False)` with `shell=False` **hard-coded** in
`ExecSink.send()` -- not a parameter you or a future caller can override.
The alert JSON is never concatenated into `command` or into any string a
shell re-parses; it is only ever written to the child process's stdin pipe.

What this does **not** protect against: `shell=False` only guarantees *this
process* doesn't hand your command line to a shell. If the command you
configure is itself something that interprets its stdin as code/templates
(e.g. `sh` reading a script from stdin, `python` with `-`, a script that
does its own `eval`/templating on what it reads), that command can still
execute content derived from the alert. Do not point `--exec-sink` at such
a command; use one that treats its stdin as an opaque string (write it to a
file, log it, pass it to a notification API that itself treats it as inert
text). llm-burnwatch does not vet what the configured command does with the
JSON it's handed -- that trust boundary is the same as any other local
script you choose to run.
