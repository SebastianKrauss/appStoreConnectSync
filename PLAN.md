# From tool to product

`ascsync` grew out of one concrete need — one iOS app, one developer, a store
presence in three languages — and it has carried that load: the first complete
push to App Store Connect covered store texts in three languages, 48
screenshots, 91 achievements with 273 icons, 7 leaderboards, 6 in-app purchases
and 11 event drafts. A second dry run afterwards found nothing left to do.

What follows is the road from "works for me" to "works for other people".

## An honest starting point

**Strengths.** The three-way diff is the core and it holds: it reliably
separates "I changed this", "somebody changed it in ASC" and "both" — and in
the last case it refuses to write. The declarations under `resources/` are
short and readable; a new domain is one file, not a refactor. Validation runs
offline without credentials, so it fits CI. And the bugs the first real push
exposed are fixed, each with its reason recorded next to the code.

**Weaknesses.** Exactly **one** app has been tested, one account, one platform
(iOS), no subscriptions. There is no handling for the cases a single user never
hits: several apps, several teams, concurrent runs, a key expiring mid-push.
And `push` is not transactional — it is idempotent, which is enough in
practice, but you have to know it.

---

## Stage 1 — usable by strangers

Without these it is a shared folder, not a product.

- [x] **English throughout.** Messages, documentation, comments.
- [ ] **Error messages that help.** Today the API's answer is often all you
      get. The dozen most common cases deserve a sentence about what to do —
      the same standard the code comments already meet.
- [ ] **`ascsync init`.** One command that builds `data/` from an app that
      already exists in ASC: a `pull` that knows nothing is there yet. Today
      you have to understand the scaffold by hand.
- [x] **`generators/achievement_template.py` is generic.** The families moved
      out of Python and into `data/gamecenter/achievement_scheme.json`: a
      suffix template, the value lists to expand, and a points lookup. The
      shipped scheme is a runnable example with placeholder ids rather than
      one game's private vocabulary.
- [x] **A license.** Apache-2.0, with LICENSE and NOTICE in place. See below.
- [x] **Supported Python versions pinned.** Floor raised to 3.10, tested in CI
      against 3.10 through 3.13. As a side effect the urllib3/LibreSSL warning
      that macOS's system 3.9 produced on every single run is gone.

## Stage 2 — dependable

- [ ] **Test coverage beyond the self-test.** The self-test covers the subtle
      parts (diff classification, occurrence expansion, asset resolution, merge
      behaviour) and needs no credentials — but it has never seen the API. What
      is missing are **recorded API responses**: capture real ones once,
      anonymise them, and run `push`/`pull` against them. Only that catches the
      class of bug the first real push exposed.
- [x] **CI.** Self-test plus `validate` on every push and pull request, across
      four Python versions, with no credentials involved — so a fork's pull
      request is checked as thoroughly as a branch. It also asserts that
      `validate --readiness` on the empty scaffold *reports* rather than
      crashes, since a fresh scaffold is supposed to be short of a submission.
- [ ] **Several apps.** `ASCSYNC_PROJECT` already covers it (one directory per
      app). What is missing is proof that it holds up, and an example in the
      documentation.
- [ ] **Several keys or teams.** Today there is exactly one set of environment
      variables. Too little for an agency with several clients.
- [ ] **Resume after an abort.** The push is idempotent, so "run it again" works
      today. Across a thousand calls a progress file would be kinder.

## Stage 3 — removing friction

- [ ] **`ascsync diff` as a readable report.** `plan` prints a list. For a
      human who has to review 48 screenshots and 90 text fields, an HTML page
      with before/after is far better — and it can be attached as a CI artifact
      or linked from a pull request.
- [ ] **Image preview.** A contact sheet per screenshot set, so you see what
      goes up before it goes up.
- [ ] **Event calendar.** A timeline of dates with publication and review
      deadlines. Precisely the arithmetic people get wrong.
- [ ] **Generate the schema from the API.** The field declarations are
      hand-maintained. Four of the six bugs in the first push were wrong field
      or relationship names. Apple's OpenAPI specification could generate them.

---

## Which license?

**Apache-2.0** — chosen, and already in the repository. Two reasons.

The first is patents. Apache-2.0 grants an explicit patent license from every
contributor and terminates it for anyone who sues over patents. MIT is silent
on the subject. For a tool that companies would run inside their release
process, that silence is exactly the kind of ambiguity a legal department
notices — and the reason many of them have a blanket preference for Apache-2.0.

The second is attribution. Apache-2.0 requires the NOTICE file to be carried
along, which keeps your name attached as the project spreads. MIT requires the
copyright notice too, but in practice it disappears more easily.

The counter-argument for **MIT** is real and worth weighing: it is short,
everyone recognises it, and it creates no friction at all. If you want maximum
adoption and do not care about patents, take MIT. Both are permissive; both let
companies use this commercially without asking.

What I would **not** choose is GPL or AGPL. This is a build tool that runs
alongside proprietary apps; a copyleft license makes companies' lawyers
nervous for no gain you actually want.

Done: `LICENSE` holds the full text, `NOTICE` carries the attribution, and
`pyproject.toml` declares both.

---

## What the API offers and `ascsync` does not

I asked the API which relationships this app actually exposes. The list is long
and the covered part is a minority. Sorted by how much I think they matter for
a tool of this shape:

**Worth having, close to the existing grain**

| Area | Why | Effort |
|---|---|---|
| ~~Subscriptions~~ | **Done** — groups, subscriptions, localizations, review screenshot. Offers and prices deliberately left in ASC. Declared against Apple's documented model; the paths are verified, the field names are not. | — |
| ~~Accessibility declarations~~ | **Done** — one record per device family, verified end to end against live data. | — |
| **Promoted in-app purchases** (`promotedPurchases`) | Which purchases appear on the product page, and in what order. An ordered list with images — the same shape as screenshots. | Small |
| **Product page optimization** (`appStoreVersionExperimentsV2`) | A/B tests of icon, screenshots and text. Treatments are content and belong in the repo; the reading of results does not. | Medium |
| **Custom EULA** (`endUserLicenseAgreement`) | A text per territory. Trivially content. | Small |
| **Prices for real** (`appPriceSchedule`) | Deliberately read-only today. With a dry run and explicit confirmation this is doable, and it removes one of the "only in ASC" items. | Medium |

**Reachable, but a different kind of thing**

- **TestFlight** (`betaGroups`, `betaTesters`, `betaAppLocalizations`,
  `betaAppReviewDetail`): this app has a group and 14 builds sitting there. The
  beta descriptions and the beta review details are genuinely content and would
  fit. Testers and groups are people and state — that is administration, not
  content, and I would keep it out.
- **Customer reviews and responses** (`customerReviews`): responses are text you
  might want reviewed before it goes public. But they are reactive and
  time-critical, which is the opposite of a repository workflow.
- **Webhooks**: interesting as a trigger — ASC notifies you, CI runs `plan`.
  That is a nice pairing with the nightly drift report.
- **App Clips**, **app tags**, **alternative distribution**, **background
  assets**: real API surface, but only relevant to apps that use them.

**Deliberately out of scope**

Analytics reports (needs a different role — this key gets 403), performance
metrics, sales and finance reports, provisioning (certificates, profiles,
devices), Xcode Cloud, users and roles. All of these are App Store Connect too,
but none of them are *content*. Pulling them in would turn a focused tool into
"an API client", which is the point at which projects like this stop being
usable.

If I had to pick two: **accessibility declarations** because they are new,
small and nobody has tooling for them yet, and **subscriptions** because their
absence is what will make people put the tool down.

---

## Where I would start

1. **License** — everything else is moot without it.
2. **Recorded API responses in the test suite** — they catch exactly the bugs
   this project paid for on its first real push.
3. **`ascsync init`** — the first impression decides whether anyone keeps it.
4. **`diff --html`** — the largest felt gain per unit of work.
5. **Accessibility declarations** — small, current, and a reason for someone
   to try this rather than something else.

Subscriptions, several teams and a frontend come when somebody asks. Everything
before that is speculation about users who do not exist yet.

---

## Does it need a web frontend?

Your own instinct is right: in the end you rebuild ASC. I would put it more
sharply — **a UI that rebuilds ASC is a race you cannot win.** Apple changes
fields, states and rules without asking. Every change that is a form field over
there is a ticket over here. The effort scales with Apple's pace, not yours.

The value is not in the interface, it is in the **repository**: diff, history,
review, reproducibility, automation. None of that needs a frontend.

**Where a UI would genuinely help** — a short list:

1. **Make the dry run readable.** Plain text is the wrong format for ninety
   changed fields. Side by side, with highlighting, would be clearly better.
2. **Show images.** A screenshot diff simply cannot be rendered in a terminal.
3. **Show dates.** A calendar beats any table.

All three are **read-only**. That is the decisive point: what helps is a
*viewer*, not an *editor*. And a viewer needs no server, no sessions, no
permissions, no key storage — it can be a static HTML file produced by
`ascsync diff --html`.

So: **no frontend, a report format instead.** A fraction of the work, nothing
to operate, no security surface, and it fits what the tool already is.

I would only build a real frontend for one specific situation: **somebody
without Git has to maintain copy.** An editor who writes descriptions but does
not use a repository. Even then the job is not "rebuild ASC" but "a form for
exactly the fields that person owns, producing a pull request". A different,
much smaller product.

## And the AI as the interface?

I think that is the stronger path, for a concrete reason: the hard part of ASC
is not typing, it is **knowledge** — how long a field may be, which enum value
is valid, what submission additionally requires, in which order things must
happen. A UI can only put that in tooltips. An agent can read the file, explain
the error, make the change, and prove with `validate` that it is right.

On top of that, the risky steps are confirmation steps anyway: show the dry
run, get consent, write. That is a conversation, not a form.

What is still missing for it:

- [ ] **Sharpen [`AGENTS.md`](AGENTS.md) against real behaviour.** The file
      exists and states the rules. Whether it is sufficient only shows when an
      agent follows it without its author sitting next to it.
- [ ] **Machine-readable output.** `--json` for `plan`, `validate` and `push`,
      so an agent does not have to parse prose. `.report.json` exists, but not
      everywhere.
- [ ] **Force the dry run.** A switch that refuses `--yes` unless a dry run
      happened in the same session. Convention today; mechanism would be
      better.
- [ ] **Log writing runs.** `.requests.log` exists; a readable "who wrote what
      when" is the audit trail you want once writes are automated.
