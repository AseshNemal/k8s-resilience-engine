# Licensing status and contributor consent

K8s Resilience Engine is released under the **Apache License 2.0** (`LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`).
This page records what is settled, what still needs written confirmation, and how to collect it. **Nothing below
is assumed to have been granted until it is recorded in `docs/LICENSE_CONSENT.md`.**

## What is settled

- The license text is the unmodified official Apache-2.0 text. The copyright notice is
  `Copyright 2026 K8s Resilience Engine Contributors` (`NOTICE`).
- Third-party licenses were reviewed and are compatible (see `THIRD_PARTY_NOTICES.md`): permissive licenses, plus
  `certifi` under MPL-2.0 used unmodified.

## What consent is required, and why

A "Contributors" copyright notice does not by itself license anyone's work. Each author still owns the copyright in
the code they wrote, and Apache-2.0 section 5 only covers contributions *submitted after* the project states its
license. Everything written before that needs an explicit grant. From each contributor we need:

1. **Authorship.** The code they committed is their own work (or they say which parts are not).
2. **License grant.** They license their contributions to the project under Apache-2.0 (copyright and the patent
   license in section 3), and agree the project's notice is `Copyright 2026 K8s Resilience Engine Contributors`.
3. **No competing claim.** No employer, sponsor, client or other third party has a claim on their contributions, and
   they included no code, text or images they are not allowed to license.
4. **AI assistance.** If AI tools generated part of their contribution, they are satisfied they may license it.

Contributors, from the repository history (names as committed; the four members listed in the README appear to be
these people, but each must confirm their own identity and any other names or accounts they used):

| Git author name(s) | Commits | Appears to be (README) |
| --- | --- | --- |
| Adriel Damian | 243 | A.D. Perera, Workflow Discovery |
| AseshNemal, Asesh Nemal | 270 | L.A. Nemal, Recommendation and Validation |
| Manuthi2002, Manuthi | 165 | P.A.M. Minara, Deployment Graph and Placement Drift |
| Yasindu Rasanga Karawita | 72 | K.V.D.Y.R. Karawita, Failure Prediction |

(Counts as of 2026-11-19; one commit carries a tool co-author trailer.)

## Does SLIIT have a claim?

**Not established.** SLIIT's *Policy on Intellectual Property* (Version 1.0, Feb 2019,
<https://static.sliit.lk/wp-content/uploads/2020/03/13102829/IP-Policy.pdf>) appears to say, in section 5.4, that students
not employed by SLIIT own the IP they create, with exceptions: work done under a sponsored or third-party agreement,
or with significant SLIIT resources or staff contribution, may start with SLIIT; and software created at SLIIT is
SLIIT's unless there is no SLIIT employee contribution or significant use of SLIIT resources. This was read from
search extracts, not the full document, so **read the policy itself** and answer these in writing:

- Was the project under a sponsor, company or other third-party agreement? Is there a project agreement or topic form
  (the repository holds `docs/TAF-J26-SE-311.pdf`) that says anything about ownership or publication?
- Did a supervisor or other SLIIT staff write code or text that is in the repository?
- Did the work use SLIIT-provided resources beyond ordinary student access (hosted servers, licensed software, funded
  cloud credits)?
- Does the module's rule book require permission before releasing a project publicly?

If any answer is yes or unclear, ask the supervisor and the SLIIT research or IP office for a **written** statement that
SLIIT either has no claim or consents to the Apache-2.0 release. Keep that message with the consent record.

## A simple consent process

1. One maintainer opens a GitHub issue "License consent for Apache-2.0" containing the statement below.
2. Each contributor replies from their own GitHub account with `I agree` (the issue comment is timestamped and tied
   to the account), and names the git author names and emails they used.
3. Record each reply in `docs/LICENSE_CONSENT.md` (contributor, account, date, link to the comment).
4. Send the SLIIT message (below) and record the answer there too.
5. Only when every row is complete and the SLIIT answer is recorded, change the status in `docs/LICENSE_CONSENT.md`
   to "Confirmed". Until then the Apache-2.0 notices stay as prepared but publishing is held.

**Statement to post:**

> I wrote the contributions made under my git author name(s) in this repository (or I have listed what I did not write).
> I license them to the K8s Resilience Engine project under the Apache License, Version 2.0, including the patent
> license in section 3, and I agree the project notice is "Copyright 2026 K8s Resilience Engine Contributors".
> No employer, sponsor or other third party has a claim on them, and nothing I contributed is code, text or images I am
> not allowed to license. If AI tools generated part of it, I am satisfied that I may license that part.

**Message to SLIIT (supervisor and research or IP office):**

> Subject: Permission to release project J26-SE-311 as open source (Apache-2.0)
>
> We are the authors of the software "K8s Resilience Engine", developed as project J26-SE-311. We intend to publish it
> as open source under the Apache License 2.0. Could you confirm in writing whether SLIIT claims any ownership or
> approval right over the software under the Policy on Intellectual Property, and, if so, give consent for this release?
> Was the project subject to any sponsor or third-party agreement we should know of?

## Open items

- **Ownership consent and SLIIT confirmation**: pending (above).
- **Product logo** (`app/static/img/logo.png`, the blue gear with a ship's-wheel mark): kept as the official product logo
  by the maintainers' decision. It resembles the Kubernetes wheel, which is a trademark of The Linux Foundation, and
  its source is not documented in this repository. **The trademark and provenance concern is not resolved.** Until it is,
  the product page makes no claim of affiliation with Kubernetes or Docker (see `THIRD_PARTY_NOTICES.md`).
