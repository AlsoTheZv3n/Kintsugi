# Compliance

Kintsugi scrapes public web data. This document states the rules the project holds itself to, and —
in the enforcement table at the end — **where each rule is enforced in code**, because a rule that
lives only in prose gets broken. Rows without code yet are marked `UNENFORCED` rather than omitted, so
the gap is visible instead of hidden.

## Scope and jurisdiction

Kintsugi targets publicly reachable pages of sources that permit automated access. It is built and
demonstrated against explicit scraping sandboxes (`books.toscrape.com`, `quotes.toscrape.com`,
`scrapethissite.com`) and, later, open-data sources with an official API for cross-checking.

**robots.txt is a protocol convention (RFC 9309), not a legal safe harbour.** Obeying it is necessary
but not sufficient: a source's Terms of Service, applicable copyright, database rights and data-
protection law all bind independently of what `robots.txt` allows. Admission of a source is a
deliberate decision recorded per source, not a default granted by a permissive robots file. This
document is engineering policy, not legal advice.

## Never do

- **No login-gated content.** If a page requires authentication, the run aborts and opens an incident.
- **No protection bypass.** No CAPTCHA solving, no anti-bot fingerprint rotation, no header spoofing to
  impersonate a real browser. A CAPTCHA or consent wall aborts the run with outcome `blocked`.
- **No personal data without a recorded legal basis.** A source that yields personal data is not
  activated until a legal-basis entry and a working deletion path exist (see below).
- **No source without a contact.** Every request carries an identifiable user agent with a contact
  address; a source cannot run without one.

## Source admission checklist

Before a source is activated:

1. Terms of Service read, and the outcome recorded with the site pack.
2. `robots.txt` fetched and archived; the relevant `User-agent`/`Disallow`/`Crawl-delay` lines noted.
3. Personal-data assessment: does the source yield personal data? If yes, legal basis and deletion
   path are mandatory before activation.
4. Identifiable user agent with a contact address configured (`KINTSUGI_CONTACT`).

## Politeness budget

Per source, declared in the site pack (`docs/02-site-packs.md`, `fetch:` block):

- `rate_limit_rps` — conservative by default; a source's `Crawl-delay` can only **raise** the interval.
- `concurrency` — low, per domain.
- `conditional_requests` — `ETag` / `If-Modified-Since` so an unchanged page costs a `304`.

## Blocked sources

A source is **blocked** (never healed, per the five-outcome table in `README.md`) when it returns a
CAPTCHA, a consent wall, a bot-detection page, or repeated `429`/`403`. Blocked means: report a fetch
problem, do **not** learn selectors from the block page. Healing a Cloudflare or cookie-banner page is
the system's most destructive failure mode and is prevented in the pre-heal check.

**Rejected source — `webscraper.io/test-sites/e-commerce/`.** Its `robots.txt` disallows the path for
all agents:

```
User-agent: *
Disallow: /test-sites/e-commerce/
Disallow: /test-sites/tables
```

It is therefore **not used**, even though the design docs once named it. The admitted replacement for
the same AJAX test case is <https://www.scrapethissite.com/pages/ajax-javascript/>, whose `robots.txt`
disallows only `/lessons/` and `/faq/`.

## Admitted sources

Sources whose compliance block has been reviewed and which are active. The
verdict and robots-checked date mirror each pack's `compliance` block.

| Source | ToS verdict | robots checked | personal data |
|---|---|---|---|
| books.toscrape.com | permits | 2026-07-21 (404 → allow all) | no |

## Personal data and deletion

For a source carrying personal data, a deletion request must reach **every** copy of a record, not
just the current row:

- the current Silver row (`valid_to IS NULL`),
- the historised Silver rows (SCD Type 2, `valid_to IS NOT NULL`),
- and the Bronze snapshots that produced them.

A deletion path that stops at the current row leaves personal data in history and in the raw blobs.
This is why deletion is a documented precondition of activation, not an afterthought.

## Fixture redistribution

Golden fixtures are stored, byte-exact snapshots of third-party pages. They are kept for regression
testing only. They are **not** redistributed as a dataset, and a source whose Terms forbid storing
copies of its pages is not added to the fixture corpus. Fixtures never carry personal data.

## robots.txt fail-modes

Parsing uses **protego** (the parser Scrapy uses), Python 3.12. The pinned behaviour on a robots
fetch:

- **404 or 410 → allow all.** An absent robots file grants access (RFC 9309 §2.3.1.3). This is not
  theoretical: `books.toscrape.com` serves HTTP 404 for `/robots.txt`, so without this rule the
  Phase 0 quickstart would fetch nothing at all.
- **5xx or timeout → deny the whole domain**, with reason `robots_unavailable`. An unreachable robots
  file is treated as "unknown", and unknown means do not crawl.
- **`Crawl-delay` raises the request interval, never lowers it.** A source may ask us to slow down; it
  can never make us go faster than our own configured rate.

## Enforcement table

Every rule gets a row. `Enforced in` is either the literal `UNENFORCED` (no runtime code yet — the
fetch and validation layers land in later Phase 0 epics) or a dotted `kintsugi.*` module path. Rows
that are enforced name a single pytest node that proves it.

| Rule | Enforced in | Failure mode | Test | Phase |
|---|---|---|---|---|
| User agent carries a contact address | `kintsugi.config` | request refused before it is sent | `tests/unit/test_config.py::test_user_agent_wird_erwartungsgemaess_gerendert` | 0 |
| Missing contact address blocks the user agent | `kintsugi.config` | `ConfigError` on first use | `tests/unit/test_config.py::test_user_agent_wirft_ohne_kontakt` | 0 |
| Secrets never surface in logs, reprs or dumps | `kintsugi.config` | leak guard fails the build | `tests/compliance/test_no_secret_leaks.py::test_keine_standarddarstellung_zeigt_den_klartext` | 0 |
| robots.txt obeyed; 404/410 allow-all, 5xx/timeout deny | `UNENFORCED` | fetch layer, E0.7 | planned `tests/compliance/test_robots_policy.py` | 0 |
| Conservative rate limit; Crawl-delay only raises it | `UNENFORCED` | fetch layer, E0.7 | planned `tests/unit/test_rate_limit.py` | 0 |
| Conditional requests (ETag / If-Modified-Since) | `UNENFORCED` | fetch layer, E0.7 | planned `tests/unit/test_conditional_requests.py` | 0 |
| CAPTCHA / consent wall aborts the run as `blocked` | `UNENFORCED` | fetch/extract, E0.7/E0.8 | planned `tests/unit/test_block_detection.py` | 0 |
| No login-gated content | `UNENFORCED` | fetch layer, E0.7 | planned `tests/unit/test_no_auth_fetch.py` | 0 |
| Blocked sources are never healed | `UNENFORCED` | pre-heal check, Phase 2 | planned `tests/mutations/test_negatives.py` | 2 |
| Personal data: deletion reaches history and bronze | `UNENFORCED` | delete path, later phase | planned `tests/integration/test_deletion_path.py` | 5 |
| Fixtures are not redistributed and carry no personal data | `UNENFORCED` | policy, manual review | — | 1 |
| robots.txt not disablable without a documented pack entry | `kintsugi.packs.model` | pack load fails validation | `tests/compliance/test_pack_compliance_block.py::test_respect_robots_false_wird_abgelehnt` | 0 |
| ToS verdict recorded, and a forbidding source is refused | `kintsugi.packs.model` | pack load fails validation | `tests/compliance/test_pack_compliance_block.py::test_tos_verdict_forbids_wird_immer_abgelehnt` | 0 |
| Personal data requires a recorded legal basis | `kintsugi.packs.model` | pack load fails validation | `tests/compliance/test_pack_compliance_block.py::test_personal_data_ohne_legal_basis_wird_abgelehnt` | 0 |
