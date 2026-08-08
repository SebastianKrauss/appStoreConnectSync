# What is left to do

`ascsync` grew out of running one real store presence: the first complete push
covered store texts in three languages, 48 screenshots, 91 achievements with
273 icons, 7 leaderboards, 6 in-app purchases and 11 event drafts, and a second
dry run afterwards found nothing left to do.

This file is the honest remainder — what the tool cannot do yet, what is
untested, and what was left out on purpose. Finished work is not listed here;
that is what the README and the commit log are for.

## Where it stands

**What holds.** The three-way diff is the core and it works: it separates "I
changed this", "somebody changed it in ASC" and "both", and in the last case it
refuses to write. A new domain is one declaration under `resources/`, not a
refactor. Validation runs offline without credentials, and a cassette of 111
recorded responses lets CI drive a full `pull` end to end with no API key.

**What does not.** Exactly **one** app has been exercised against the live API,
on one account, on iOS. Subscriptions are declared from Apple's documentation
rather than from a live subscription — the paths answer, the field names are
unverified. There is no `push` cassette, so writing is covered by reasoning and
one real run, not by a test. And `push` is not transactional: it is idempotent,
which is enough in practice, but you have to know it.

---

## Usable by more people

- [ ] **Several keys or teams.** Two apps in one account share a key, and that
      works today. Two *accounts* mean swapping three environment variables by
      hand — fine for one person, thin for an agency. Named profiles in a
      config file would fix it.

## Dependable

- [ ] **A `push` cassette.** Reading is covered; writing is not. Recording one
      needs an account somebody is willing to write to, which is the only
      reason it does not exist yet.
- [ ] **Resume after an abort.** The push is idempotent, so "run it again"
      works. Across a thousand calls a progress file would be kinder.
- [ ] **Exercise subscriptions against a live one.** The field names are now
      confirmed against Apple's specification, so the declaration is no longer
      a guess — but nothing has actually created a subscription group through
      it. The remaining risk is behavioural, not structural.

## Friction


## For running it through an agent

- [ ] **Sharpen [`AGENTS.md`](AGENTS.md) against real behaviour.** The rules
      are written. Whether they are sufficient only shows when an agent follows
      them without their author sitting next to it.

---

## What the API offers and `ascsync` does not

Asked the API which relationships an app actually exposes; the covered part is
a minority. Sorted by how much they matter for a tool of this shape.

**Worth having, close to the existing grain**

| Area | Why | Effort |
|---|---|---|
| **Promoted in-app purchases** (`promotedPurchases`) | Which purchases appear on the product page, and in what order. An ordered list with images — the same shape as screenshots. | Small |
| **Custom EULA** (`endUserLicenseAgreement`) | A text per territory. Trivially content. | Small |
| **Product page optimization** (`appStoreVersionExperimentsV2`) | A/B tests of icon, screenshots and text. The treatments are content and belong in the repo; reading the results does not. | Medium |
| **Prices for real** (`appPriceSchedule`) | Read-only on purpose today. With a dry run and explicit confirmation it is doable, and it removes one of the "only in ASC" items. | Medium |

**Reachable, but a different kind of thing**

- **TestFlight** (`betaAppLocalizations`, `betaAppReviewDetail`): the beta
  description and beta review details are genuinely content and would fit.
  Testers and groups are people and state — administration, not content, and I
  would keep them out.
- **Customer reviews and responses**: a response is text you might want
  reviewed before it goes public. But it is reactive and time-critical, which
  is the opposite of a repository workflow.
- **Webhooks**: interesting as a trigger — ASC notifies, CI runs `plan`. A good
  pairing with a nightly drift report.
- **App Clips**, **app tags**, **alternative distribution**, **background
  assets**: real surface, relevant only to apps that use them.

**Deliberately out of scope**

Analytics and sales reports, performance metrics, provisioning (certificates,
profiles, devices), Xcode Cloud, users and roles. All of it is App Store
Connect too, and none of it is *content*. Pulling it in would turn a focused
tool into "an API client", which is the point where projects like this stop
being usable.

---

## Does it need a web frontend?

Probably not, and the reason is worth stating: **a UI that rebuilds App Store
Connect is a race you cannot win.** Apple changes fields, states and rules
without asking. Every change that is a form field over there becomes a ticket
over here. The effort scales with Apple's pace, not yours.

The value is not in an interface, it is in the **repository**: diff, history,
review, reproducibility, automation. None of that needs a frontend.

Where a UI genuinely helps is a short list — make a large plan readable, show
images, show dates. All three are **read-only**. What helps is a *viewer*, not
an *editor*, and a viewer needs no server, no sessions, no permissions and no
key storage. `plan --html` is that viewer, and it is a static file.

The one situation that would justify a real frontend: **somebody without Git
has to maintain copy.** An editor who writes descriptions but does not use a
repository. Even then the job is not "rebuild ASC" but "a form for exactly the
fields that person owns, producing a pull request" — a different and much
smaller product.

## The agent as the interface

This is the more promising direction, for a concrete reason: the hard part of
App Store Connect is not typing, it is **knowledge** — how long a field may be,
which enum value is valid, what submission additionally requires, in which
order things must happen. A UI can only put that in tooltips. An agent can read
the file, explain the error, make the change, and prove with `validate` that it
is right.

The risky steps are confirmation steps anyway: show the dry run, get consent,
write. That is a conversation, not a form. What is still missing for it is the
"For running it through an agent" list above.
