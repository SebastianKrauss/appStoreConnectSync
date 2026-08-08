# ascsync — App Store Connect as content in your Git repository

Store texts, screenshots, previews, Game Center achievements and leaderboards,
in-app purchases, in-app events, review details, app privacy and availability —
kept as JSON and image files in **your** repository, with diffs, review and
history. `ascsync` reconciles them with App Store Connect.

The point is not to replace the web interface. The point is that your store
content travels the same road as your code: visible in a diff, traceable in the
log, recoverable from history — and reproducible, instead of clicked once and
forgotten.

> Running it through an AI agent (recommended): [`AGENTS.md`](AGENTS.md).
> What it still needs to become a product: [`PLAN.md`](PLAN.md).

## The one rule

**`pull` first, then `plan`, then `push --yes`.**

`push` without `--yes` is a dry run. Without a prior `pull` there is no
snapshot, and the diff cannot tell a hand edit made in ASC from one of your
own — it will still write, but it warns.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

That puts `ascsync` on the venv's path; `python -m ascsync` works too.

### Getting API access

App Store Connect does not authenticate `ascsync` as a person. It authenticates
a **key**, and the key carries a role. You do not need to invent a service
account or a shared Apple ID — and you should not, because a second Apple ID
means a second set of two-factor prompts that no automation survives.

In App Store Connect, go to **Users and Access → Integrations → App Store
Connect API**. Two things live on that page:

**The Issuer ID** sits at the top, above the list, and looks like
`a1b2c3d4-5e6f-7890-abcd-ef1234567890`. It identifies your team and is the same
for every key you ever create. Copy it once. It is not a secret in the way the
private key is — it is useless on its own — but there is no reason to publish
it either.

**The key itself** you create with *Generate API Key* (the `+` button). Give it
a name you will still understand in a year — `ascsync (laptop)` beats `key1` —
and pick a role:

| Role | Enough for |
|---|---|
| **App Manager** | everything `ascsync` does: store texts, screenshots, Game Center, in-app purchases, events, submission. **Pick this.** |
| Developer | too little — cannot edit store metadata |
| Admin | more than needed; avoid on principle |
| Finance / Sales | only for the reporting APIs, which `ascsync` does not use |

Choose **Team Keys**, not an individual key, unless you are certain you want
the key to die with your own account. A team key belongs to the team and keeps
working when a person leaves.

After creating it, the row shows the **Key ID** (ten characters) and a
download link for the private key. **You can download the
`.p8` exactly once.** Lose it and there is no recovery — you revoke the key and
generate a new one. That is not a disaster, it is just annoying, and it is why
the file belongs somewhere you back up.

Store it outside the repository:

```bash
mkdir -p ~/.appstoreconnect/private_keys
chmod 700 ~/.appstoreconnect ~/.appstoreconnect/private_keys
mv ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.appstoreconnect/private_keys/
chmod 600 ~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8
```

That directory is also where `xcrun altool` and `notarytool` look by default,
so a later build upload finds the key without being told. `*.p8` is git-ignored
here, but the key still does not belong in a repository — the ignore rule is a
seatbelt, not a policy.

Then export the three values:

```bash
export ASC_ISSUER_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export ASC_KEY_ID="XXXXXXXXXX"
export ASC_PRIVATE_KEY_PATH="~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8"
```

Note that the key ID is also in the filename, which is the convention Apple's
own tools rely on — keep the name as downloaded.

**In CI**, pass the key as a secret rather than a file: base64-encode it
(`base64 -i AuthKey_XXXXXXXXXX.p8`), store that as a secret, and write it back
to a temporary file at the start of the job. Give CI its own key with its own
name, so you can revoke it without touching your laptop's access. Revoking is
immediate and per key: *Users and Access → Integrations →* the row's context
menu.

Keys do not expire. Rotate one if it may have leaked, when someone with access
leaves, or on whatever schedule your own policy demands.

### Point it at your app

```bash
cd ~/projects/myapp-store        # an empty directory, ideally its own repo
ascsync init --bundle-id com.example.myapp --locales en-US,de-DE
```

`init` writes `data/app.json` and `data/locales.json`, creates `assets/`, and —
if credentials are in the environment — pulls everything App Store Connect
already knows about the app on top. A full pull is right at this moment and at
no other: `data/` is empty, so there is no local text an empty ASC field could
overwrite.

Without credentials it scaffolds and says so; `--no-pull` skips the fetch
deliberately.

Then check the connection:

```bash
.venv/bin/ascsync doctor
```

`doctor` reports access, app id, languages, version states, the Game Center
parent and your rate limit, and tells you whether an editable version exists
at all.

Examples below write just `ascsync`:

```bash
alias ascsync='.venv/bin/ascsync'
```

## The first run

```bash
ascsync pull --snapshot-only   # ASC state into .snapshot/, data/ untouched
ascsync plan                   # field by field: where do repo and ASC differ?
```

**`--snapshot-only` matters the first time.** A full `pull` merges the ASC
state into `data/`, and an empty field in ASC will overwrite your local text.
If you have already written your descriptions, that is data loss. The other way
round — empty repo, populated ASC — a full `pull` is exactly right, because it
does the typing for you.

## Commands

| Command | Effect |
|---|---|
| `ascsync init --bundle-id …` | create a project here and fill it from ASC |
| `ascsync doctor` | access, role, rate limit, app and version state |
| `ascsync pull [--domain …]` | ASC state into `data/` (merged) **and** `.snapshot/` |
| `ascsync pull --snapshot-only` | `.snapshot/` only; `data/` untouched |
| `ascsync plan [--domain …] [--html FILE]` | three-way diff, writes nothing, exit 2 on drift |
| `ascsync push [--domain …] --yes` | write; without `--yes` it is a dry run |
| `ascsync push --yes --require-dry-run` | refuse to write unless a dry run saw this exact plan |
| `ascsync validate [--domain …]` | offline: limits, assets, languages, source cross-check |
| `ascsync validate --readiness` | additionally: what **submission** requires |
| `ascsync achievements template` | achievement scaffold from a declared id scheme |
| `ascsync events generate --ahead 12w` | event drafts, one per leaderboard occurrence |
| `ascsync events calendar [--weeks 26]` | when each event submits, publishes and runs |
| `ascsync releases --yes` | release achievements/leaderboards for publication |
| `ascsync privacy publish --yes` | publish app privacy (deliberately its own step) |
| `ascsync submit --version 1.0 [--send] --yes` | submit a version or event for review |

Domains for `--domain`: `store`, `gamecenter`, `iap`, `subscriptions`,
`events`, `accessibility`, `pricing`, `privacy`, `pages`. Further filters:
`--only <key>` (repeatable),
`--only-locale de-DE`, `--version 1.0`, `--skip-assets`.

`--json` puts the report on stdout and moves the prose to stderr, so a script
or an agent never has to parse sentences. It works on `plan`, `push` and
`validate`.

`--require-dry-run` makes the habit a rule: `push --yes` then walks once
without writing, and refuses unless a dry run has already seen that exact plan.
Change anything in between and the receipt no longer matches — which is the
point, because the plan you approved is not the plan that would run. It is
opt-in, so it stops a tired human or an over-eager agent, not an attacker.

Every write appends a line to `.writes.log`: timestamp, command, action, path.
`.requests.log` answers "what did the tool send"; this answers the question you
have three weeks later — who changed the German description, and when.

`--html` writes the same plan as a page instead of a list: grouped by domain,
conflicts and drift first, counts up top, and every screenshot it would upload
shown rather than named. One self-contained file — no scripts, no external
requests — so it can be attached to a CI run or linked from a pull request.
Four hundred actions are unreadable in a terminal and fine on a page.

Exit codes: `0` all good · `1` error · `2` drift, conflict or overhang found.
A nightly read-only report is just `ascsync plan` plus a check for exit 2;
details land in `.report.json`.

## Layout

```
.
├── pyproject.toml        # package definition, entry point `ascsync`
├── src/ascsync/
│   ├── cli.py                the CLI
│   ├── svg2icon.py           SVG -> Game Center icon (usable on its own)
│   ├── core/                 auth, HTTP, registry, diff, planner, assets, report
│   ├── resources/            one declaration per domain (fields, limits, gates)
│   └── generators/           event drafts, achievement scaffold
├── data/                 # ← you edit here
│   ├── locales.json          one language list for ALL domains
│   ├── app.json              bundleId, idPrefix, categories, source cross-check
│   ├── store/                app_info.json, versions.json, custom_pages.json
│   ├── gamecenter/           achievements.json, achievement_scheme.json,
│   │                         leaderboards.json, leaderboard_sets.json
│   ├── iap.json  subscriptions.json  accessibility.json
│   ├── pricing.json  privacy.json
│   └── events/               events.json (generated), templates.json (maintained)
├── assets/               # ← images and videos live here
│   ├── screenshots/<locale>/<display type>/01_*.png
│   ├── previews/<locale>/<display type>/01_*.mp4
│   ├── gamecenter/achievements/<id without prefix>.png
│   ├── gamecenter/leaderboards/<id without prefix>.png
│   ├── iap/review/<product without prefix>.png
│   ├── subscriptions/review/<product without prefix>.png
│   └── events/<variant>/<locale>/card.png|detail.png
├── .snapshot/            # last pulled ASC state; commit it, never hand-edit it
├── assets.lock.json      # which file is which asset in ASC
└── tests/selftest.py     # offline, no credentials
```

The display type is a **directory name** (`APP_IPHONE_67`,
`APP_IPAD_PRO_3GEN_129`), not a filename prefix. A new class means a new
directory; for the size check also add it to `SCREENSHOT_SIZES` in
[`src/ascsync/resources/app_store.py`](src/ascsync/resources/app_store.py).

**Where content lives:** in the current working directory. One installed
`ascsync` therefore serves any number of apps — one directory per app, one
repository per directory:

```bash
cd ~/projects/one-store && ascsync plan      # com.example.one
cd ~/projects/two-store && ascsync plan      # com.example.two
```

`ASCSYNC_PROJECT` names the directory explicitly, wherever you happen to
stand — useful in CI, or when the store repository is not where you work:

```bash
ASCSYNC_PROJECT=~/projects/one-store ascsync validate
```

Nothing is shared between two projects: `data/`, `assets/`, `.snapshot/` and
`assets.lock.json` all live inside their own directory. The self-test exercises
two side by side rather than taking the claim on trust.

Two apps in the same App Store Connect account use the same key, so the same
three environment variables. Two *accounts* need two sets — export them per
shell, or per CI job.

## Data format

Every file under `data/` looks the same:

```json
{
  "resource": "gameCenterAchievements",
  "key": "vendorIdentifier",
  "items": [
    { "vendorIdentifier": "com.example.app.first.win", "points": 10,
      "localizations": { "en-US": { "name": "First win" } } }
  ]
}
```

Records are keyed by natural keys (`vendorIdentifier`, `productId`,
`versionString`, `locale`, `referenceName`) — **ASC ids live only in the
snapshot**. A `readonly` block holds fields ASC assigns (`eventState`,
`appStoreState`, …) and is never written. An empty text field means "leave the
ASC value alone", not "delete it".

Every file accepts a `_comment` key. Use it: it survives the pull and is the
only place where the reasoning behind a decision can live.

## What `plan` and `push` do

| Case | Action |
|---|---|
| desired = snapshot = remote | `ok` |
| desired ≠ snapshot, remote = snapshot | `update`/`create` — will be written |
| desired = snapshot, remote ≠ snapshot | `drift` — someone edited in ASC; `pull` takes it |
| both changed | `conflict` — **not** written |
| in ASC, not local | `overhang` — reported, never deleted |
| state locks the field | `blocked` — e.g. version "Waiting for Review" |

Deletion happens only inside **ordered asset sets** (screenshots, previews,
event assets): there the local list is the truth, otherwise a removed image
would linger on the product page.

## Two levels of validation

`ascsync validate` checks what the **API** would reject: character limits,
unknown enum values, incomplete localizations, missing or malformed assets,
language coverage — and, if configured, the ids against your app's source.

`ascsync validate --readiness` additionally checks what **submission**
requires but the API leaves optional: support URL, privacy policy URL,
copyright, description, keywords, review contact, age rating, category, at
least one screenshot per language and required display type, app privacy, a
non-empty snapshot, and the recurrence of recurring leaderboards. It closes
with the things that cannot be checked offline at all.

Both run without credentials and are therefore suitable for CI.

## Cross-checking against your source

Optional, via `data/app.json`:

```json
"code": {
  "sourceDir": "../MyApp",
  "labelsDir": "Resources/labels",
  "sourceSuffix": ".swift"
}
```

`validate` then collects every string literal starting with your `idPrefix`
and reports achievements or leaderboards the code knows and `data/` does not —
or the reverse. Interpolated literals (`"…app.level.\(index)"`) are turned
into patterns so generated families are not falsely reported missing. Without
the section the check is simply skipped.

## Limits

- **App privacy** is not reachable through the API everywhere. If
  `pull --domain privacy` answers 404 ("the relationship 'dataUsages' does not
  exist"), maintain it in the web interface; `data/privacy.json` then only
  documents it.
- **Prices** are deliberately read, never written. That includes subscription
  prices, introductory offers and promotional offers: they are priced through
  per-territory price points, and a wrong write changes what real customers
  pay.
- **Age rating** cannot be created by the API; the first record has to be made
  in ASC.
- **Submitting** is a separate, explicit step and never happens as a side
  effect.
- Game Center **releases** hang off the `gameCenterDetail`, not off an app
  version — which is why `ascsync releases` has no `--version`.
- The **subscriptions** declaration was written against Apple's documented
  model, not against a live subscription: the paths are verified, the field
  names are not. If a push answers "is not an attribute", correct
  `resources/subscriptions.py` — `.snapshot/` after a pull is the truth about
  the fields ASC knows.

## Tests

```bash
.venv/bin/python tests/selftest.py     # offline reasoning: diff, limits, generators
.venv/bin/python tests/test_replay.py  # replays recorded API traffic
```

Both run without credentials. The self-test works against its own fixtures
rather than `data/` — it tests the tool, not the contents of your project.

The replay test drives `pull` end to end against a **cassette**: real App Store
Connect responses, recorded once and committed. That is the only place where
paths, relationship names and the parsing of real bodies are exercised
together, and it exists because the offline tests could not have caught a
single one of the six bugs the first real push exposed.

Record your own, for your own account:

```bash
ASCSYNC_CASSETTE=tests/cassettes/pull.json ASCSYNC_CASSETTE_MODE=record \
    ascsync pull --snapshot-only
```

The recorder throws your content away before writing: every free-text value is
replaced, and the bundle id and app id are renamed to `com.example.app`. What
survives is structure — which is what the test is about. A cassette will never
catch a typo in your app description; it will catch a field that does not
exist.
