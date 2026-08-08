# AGENTS.md — operating `ascsync` through an AI agent

This repository is built to be driven by an **agent**. The interface is the
conversation: the user says what the store should say, the agent edits JSON,
validates offline, shows the dry run, and writes only after an explicit yes.

This file is the operating manual for that. It addresses you, the agent.
Driving it by hand is described in [`README.md`](README.md).

## Why an agent fits here

The schemas are large and unfriendly: per-field character limits, enum values,
relationships between resources, language completeness, image dimensions, a
state machine per version and per event. Exactly the kind of knowledge a person
does not keep in their head and for which a user interface becomes either
cluttered or incomplete. An agent can read the file instead, explain the error,
make the change — and `validate` says objectively whether it is right.

## The rules, none of them negotiable

**1. Never write to ASC without explicit consent.** `push`, `releases`,
`privacy publish` and `submit` are outward-facing and only partly reversible.
Show the dry run first (`push` without `--yes`), summarise what would happen,
and wait for a clear yes. Consent covers **this** run, not the next one.

**2. `submit` is its own decision.** It hands work to Apple for review. Ask for
it separately, even if the push was just approved.

**3. You do not touch credentials.** `ASC_ISSUER_ID`, `ASC_KEY_ID` and
`ASC_PRIVATE_KEY_PATH` come from the environment. You never read the contents
of the `.p8`, never print it, never copy it anywhere.

**4. A current snapshot before every push.** Without a `pull` the diff cannot
tell a hand edit in ASC from one of your own. The first time — and whenever
local texts are already written — use `pull --snapshot-only`, or an empty ASC
field will overwrite the local text.

**5. Report honestly.** If a push reports errors, quote them. If you skipped
something, say so. "Done" means verified, not hoped for.

## The usual sequence

```bash
ascsync validate                 # offline, costs nothing, catches most of it
ascsync pull --snapshot-only     # fetch ASC state, spare data/
ascsync push --domain <d>        # dry run — show it to the user
# ... wait for consent ...
ascsync push --domain <d> --yes  # write
ascsync push --domain <d>        # check: this must find nothing left to do
```

That last step is the most important one and the most often skipped. A push
that finds work again on the second run is not finished — it will repeat that
work every time. Check it, and report the result.

Order on a first run: `store` → `gamecenter` → `iap` → `releases` → `events`.
Releases have to come after the Game Center push, or there is nothing to
release.

## What you may decide yourself

- editing JSON under `data/`, writing copy, keeping to the limits
- `validate`, `plan`, `pull`, `doctor`, every dry run — all read-only
- `events generate`, `achievements template` — these only write local files
- inspecting images, measuring dimensions, stripping alpha channels

## What you must ask about

- every write to ASC
- changing version numbers
- moving dates that already exist in ASC
- deleting anything, including locally, when it is not reproducible

## Traps that will catch you otherwise

**The dry run does not show everything.** It knows nothing about a genuine
creation and nothing about the API's answer. Wrong field names, missing
relationships and invalid enum values only surface on the first real `push`.
Never schedule a domain's first push for the last minute before a deadline.

**Every image is replaced once on the first push.** Game Center and event
images carry no checksum in the API; the mapping lives in `assets.lock.json`,
and that file only comes into existence with the first push. This is normal
and not data loss — explain it rather than hiding it.

**`events generate` leaves existing events alone.** That is deliberate, so
hand-edited copy is not overwritten. After a change to `templates.json` you
have to empty `events.json` and regenerate, or the old texts stay.

**A relationship without `include` looks empty.** The API returns only a link
for relationships, no `data`, unless you ask with `include`. Miss that and you
will read a value that is set as empty and rewrite it on every run. This
applies to your own inspection just as much as to the code.

**Empty text fields delete nothing.** They mean "do not touch". Removing a text
for real has to be done in ASC.

## When something goes wrong

`push` is not transactional. If it breaks in the middle, part of the work is
written. That is manageable because every run is idempotent: fix the cause, run
the same command again, it continues. Do not invent a repair — read the error,
it names the resource, the field and the reason.

On `409` or `400` with "is not an attribute" / "is not a relationship" the
fault is in the declaration under `resources/`, not in the data. Check against
`.snapshot/`: what ASC returns on a pull is the truth about the fields it
knows.

## Tone

The user knows their product but rarely the API. Speak in their terms: not
"patched appStoreVersionLocalizations" but "the German description is in place
now". Give numbers when they mean something — "48 screenshots uploaded" can be
checked, "all done" cannot.

And when you notice something on the way that is not part of the task — a dead
link, a text over its limit, an image with an alpha channel — say so. That is
precisely where an agent is worth more here than a form.
