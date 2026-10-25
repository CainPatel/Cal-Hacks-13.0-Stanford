# Draw design system

Source of truth: the approved mockup, `design/mockup.html` (Oct 24, 2026), as changed by the redesign decisions of Oct 24 recorded here (top bar, colour categories, phrases in lists, honest labels on seeded data). Tokens live in `web/app/globals.css`; this file says what they mean and how to use them. Where this file and the spec or the mockup disagree, this file wins.

The idea: the GC's own ledger, checked line by line. Ruled rows, tallied totals, a mark in the margin beside every check, and the proof printed next to every hold. It's a serious tool for an office manager, not a SaaS dashboard. Prefer a diagram to a paragraph, a phrase to a sentence, and a word to a phrase title.

## Colour

### Tokens

| Role | Hex | CSS variable | Tailwind |
|---|---|---|---|
| Ground (page) | `#f4f2ee` | `--background` | `bg-background` |
| Sheet (panes, cards, documents) | `#ffffff` | `--sheet`, `--card`, `--popover` | `bg-sheet` |
| Ink (text, heavy rules) | `#1c1a17` | `--foreground` | `text-foreground`, `border-foreground` |
| Quiet ink (secondary text) | `#615c54` | `--muted-foreground` | `text-muted-foreground` |
| Rule (hairlines) | `#dcd7ce` | `--border` | `border-border` |
| Strong rule (inputs, margin rules, document frames) | `#b9b2a5` | `--border-strong`, `--input` | `border-border-strong` |
| Tint (hover, placeholders, document headers) | `#ebe7e0` | `--muted`, `--secondary`, `--accent` | `bg-muted` |
| Action accent (blueprint blue) | `#1f4fa3` | `--primary`, `--ring` | `bg-primary`, `text-primary` |
| On accent | `#ffffff` | `--primary-foreground` | `text-primary-foreground` |
| Selected row | `#e7edf8` | `--selected` | `bg-selected` |

| Status tone | Text / icon | Soft background | Tailwind |
|---|---|---|---|
| Pass | `#2e6b3a` | `#e2efe4` | `text-pass`, `bg-pass-soft` |
| Hold (needs your OK) | `#8a5800` | `#f5e6c3` | `text-hold`, `bg-hold-soft` |
| Fail | `#a3271b` | `#f8e2de` | `text-fail`, `bg-fail-soft` (`--destructive` = fail) |
| Pending | `#615c54` | `#ebe7e0` | `text-pending`, `bg-pending-soft` |

Fills for the top bar's segments, where a mark sits on a large area of colour:

| Fill | Hex | CSS variable | Tailwind | Notes |
|---|---|---|---|---|
| Pass fill | `#2e6b3a` | `--pass` | `bg-pass` | white text |
| Hold fill | `#d39b2f` | `--hold-fill` | `bg-hold-fill` | ink text; below 3:1 on white, so always labelled |
| Pending fill | `#7d776d` | `--pending-fill` | `bg-pending-fill` | always with the diagonal hatch (`.hatch`) |
| Fail fill | `#a3271b` | `--fail` | `bg-fail` | white text |

The fills were checked with the colour-blindness validator: lightened amber and a darker gray keep red, amber and gray apart for red-green colour blindness. Gray sits between amber and red in any row of segments, and gray always carries the hatch.

Every text/background pair above is WCAG AA at body size (amber on its soft background is the tightest, at about 4.9:1).

Note that shadcn's `--accent` is the neutral hover tint, not the blue. The blue is `--primary`.

### Status categories

Every pay request is in exactly one category. The map from rules to categories lives in one place, `web/lib/labels.ts` (`RULE_CATEGORY`, `CATEGORY`, `category()`); components never decide it themselves.

| Category | Word (always shown) | Mark | Tone | When |
|---|---|---|---|---|
| Possible fraud | "Possible fraud or wrong amount" | cross | fail | On hold, and any of these failed: cumulative math (01), contract ceiling (02), retainage (03), bank change (10), lookalike domain (11), duplicate (12), over-billing vs video (13); or vendor identity (09) came back "mismatch" or "flagged" |
| Waiting | "Waiting for paperwork" | pause bars | pending, hatched fill | On hold, and only these failed: conditional release (04), prior unconditional release (05), insurance (06), supplier release (07), owner's draw (08), identity not checked yet (09 with CRS "unknown") |
| Needs your OK | "Needs your OK" | ringed dot | hold | Escalated: over the limit (14), unsure review (15), first payment to a new sub, or a new bank account |
| Ready | "Ready to pay" | tick | pass | Every check passed and it's under the limit. Ready means cleared to pay; it does not mean paid |
| Paid | "Paid" | filled tick | pass | Only when the log has a payment for it |
| Not checked | "Not checked" | dashed ring | pending, no fill | No decision yet |

Order everywhere (top bar, board, list): possible fraud, waiting, needs your OK, not checked, ready, paid. The ones that most need help come first.

### What colour means

- **Blue means "you can act here".** It's for primary buttons, links, the focus ring, the selected row's left bar, the active tab underline, the top bar's active toggle and the dial's thumb. Nothing else is blue: no blue headings, icons, charts or decoration.
- **Green, amber, red and gray mean status, and only status**, as the categories above say. Red is also used for one specific bad thing in evidence (the forged letter, an attack that got paid).
- **Colour is never the only signal.** A status is always a mark plus a word; a segment or column always carries its word and count. Red text is always next to words that say what's wrong.
- **Everything else is ink on stone.** Hierarchy comes from size, weight, rules and space.

## Type

Geist Sans for everything people read. Geist Mono only for money, percentages, account endings, item numbers, file names and the raw trace. Mono numbers always use `tabular-nums` (`.tnum`, or the `.mono`/`.num` pattern) so columns line up to the cent. Inside running text (a sentence), numbers stay in Geist Sans with `tabular-nums`: Geist Mono gives a comma a full-width cell, so "$86,268,768.41" reads as if it had spaces around the commas.

| Token | Size / line height | Weight | Used for |
|---|---|---|---|
| `text-meta` | 15px / 1.4 | 400 | Captions, chip text, document field labels, table headers |
| `text-body` | 18px / 1.5 (17px under 720px) | 400 | Body, row phrases, checklist lines |
| `text-say` | 21px / 1.4 | 500 | The status sentence in a detail pane; section headings on Security |
| `text-title` | 24px / 1.25 | 600 | Sub name in the detail pane |
| `text-amount` | 32px / 1.1 | 500 mono | The amount in the detail pane |
| `text-lede` | 34px / 1.25 (26–27px on phone) | 600 | The page heading (on Pay requests, the month) |
| `text-hero` | 40–64px fluid / 1.05 | 500 mono | The amount on Approve (and on the key's own screen) |

- Headings use −0.01em to −0.02em tracking. Body uses none.
- Line length stays under about 64ch for prose.
- Sentence case everywhere.

## Spacing

An 8px base. In Tailwind units: 2 = 8px, 4 = 16px, 6 = 24px, 10 = 40px, 16 = 64px.

- Page gutter: 32px desktop, 16px under 720px. Max content width 1440px.
- Between job sections, and between Security sections: 48–56px.
- Inside a detail pane: 32px padding (16px when it's a phone sheet). Between pane sections: a hairline and 24–32px.
- Rows: 16px vertical, 12px horizontal. Checklist lines: 11px vertical.
- Tap targets: at least 48px tall (buttons 52px, tabs 64px desktop / 52px phone, `summary` 48px, the top bar toggle 48px).

## Radii

- `rounded-lg` (8px): buttons, chips, panes, cards, evidence boxes, the limit dial's inputs.
- 4px: source document frames, the trace block and the top bar's segments (the ends of a bar). They're paper, so they're nearly square.
- 14px: only the drawing of the approval key (it's a device).
- Hairline rules have no radius. Nothing is pill-shaped.

## Rules (lines)

- 1px `border` between rows, checklist lines and table rows.
- 2px `foreground` under a job heading and under table headers (the ledger's heading rule).
- 3px double `foreground` under a total: job totals and the "All attacks" total.
- 1px `border-strong` for the vertical margin rule beside checklist marks, input borders and document frames.

## Components

**Top bar (tabs).** White sheet, hairline below. "Draw" (24px, 700) and the month. No demo label. Tabs on the right, in this order: Pay requests, Approve, Upload Portal, Log, Security. Quiet text, the active one in ink with a 3px blue underline. On a phone the tabs wrap to their own row and scroll sideways.

**Month overview (top of Pay requests).** Replaces the opening sentence. The page heading is the month. Below it, a two-button toggle, "By job" / "By status" (`aria-pressed`, 48px, the active one filled blue). Only one view shows at a time; the choice lives in the URL (`?v=status`; by job is the default).
- *By job*: one row per job, worst job first: the job name (a link to that job's section in the list), a bar of its requests split by category (4px rounded ends, 2px gaps between segments, the hatch on waiting), then the counts with marks. On a phone the name sits above its bar.
- *By status*: one column per category that has requests, in category order. Each column has a heading rule in its tone, then mark, word and count, then one row per request: short company name and amount in mono. A row opens that request. Two columns on a phone.
- Under either view, a legend: mark, word, count and dollars per category.

**Status chip.** 28px tall, 8px radius, 15px semibold, mark plus the category's word, tone on its soft background (waiting: pending tone; not checked: no fill, dashed ring). The words are exactly the category words above.

**Request row.** The status chip on the left, then the sub name (semibold), then a short phrase (not a sentence: "Lookalike email domain", "Over your $25,000 limit", "Supplier release missing"), then the amount (mono, right-aligned, with `$`). The whole row is the button. Hover: tint. Selected: `bg-selected` plus a 4px blue bar on the left (inset shadow). Order inside a job: category order, larger amounts first.

**Job section.** Collapsible. Job name (21px, 600) with a 2px ink rule under it, rows with hairlines, then "Total this period" with the total in mono over a double rule. Jobs in order of how much they need help.

**Detail pane.** Desktop (≥1100px): a sticky white pane on the right, 6/10 of the width. Under 1100px: a full-screen sheet with "Back to pay requests" at the top; it opens with a 200ms rise and focus moves to the sub's name. Before a request is picked, the pane lists every company (by job, with chip and amount) instead of sitting empty. Order once picked:
1. Sub name, job and period, amount.
2. The status: chip plus one full plain sentence ("On hold: an email that looks like it's from Brightline asks us to pay a new bank account.").
3. The next action (see Buttons).
4. The proof, as evidence boxes (below).
5. Lines on this request.
6. Timeline and review (Jev) panels.
7. Every check, at the bottom, collapsed to its count until opened.
8. "Show details".

**Evidence boxes.** One collapsible box per piece of proof, in this order: "Lines billed this period", then each disputed line's "Line 09-20: billed vs. site video" indented under it (it's a closer look at a line), then "Email sender domain", "Bank account", "Supplier notice". Each box header has a mark, a word and its tone: fail tone (light red) when the box is the reason for a hold, pass tone (light green) when it checks out. The box is open when it explains a hold, closed when it passed. Inside, the existing evidence layouts:
- Progress: a 0–100% scale. The site-video value is an ink mark, the billed value an amber mark, the gap between them an amber bar, and the allowed tolerance a hatched band around the video value. Tick labels in mono. One phrase below ("25 points apart, 10 allowed").
- Lookalike domain: both domains in mono, one letter per cell, the wrong letter boxed in red. Then the letters named in mono, because in Geist Sans a capital I and a lowercase l look the same.
- Bank account: `ending 4869 → ending 0724`. Never the full number, anywhere, including inside a quoted email.
- Supplier: a two-line checklist (notice filed, with a tick; release missing, with a cross).
- Source documents are drawn as paper: a white frame with a strong rule, a tinted header with the file name in mono. When the original file is in the Blob store, the header links to it.

**Timeline.** In the detail pane, a vertical list of what happened to this request, built only from the event log: document received, read, reviewed, checked, signed, paid, each with its time. Steps that haven't happened are not drawn.

**Review (Jev) panel.** Each question Jev was asked (quiet), then its answer in words (score answers shown as their nearest level, never a raw number), then the confidence as a percent (mono), and whether it met the confidence floor. The timeline shows the latest review and the current decision once; earlier ones that a re-check replaced aren't drawn. Before any review has run: "No review has run on this request yet."

**Waiting for documents.** A list fed by email intake: what came in, from whom, and what still needs a person (unreadable or unmatched attachments), each with what to do.

**Checklist with margin marks.** Each check is a row with a 44px margin column, separated by a vertical strong rule. The margin holds one upright mark: a green tick (passed), a red cross (failed), or an amber ring (needs the owner). There's also screen-reader text ("Passed:", "Didn't pass:", "Needs the owner:"). The line text is a bold plain-English name ("Bank details.") followed by the rule's message. Groups: "Needs attention" first, then "Passed". The heading counts them ("15 checks, 12 passed").

**Lines table.** Item (mono), description, billed %, video %, to date. A disputed line gets the amber soft background, with the billed figure in amber.

**Show details.** A `details` element whose summary looks like a link. Inside, the raw kernel trace in 13px mono on tint. This is the only place rule codes appear on the office manager's screens.

**Buttons.** Primary: blue fill, white 18px semibold text, 52px tall, 8px radius. Secondary: blue outline with blue text, transparent fill. Link: blue underlined text. Labels say exactly what happens ("Send to the approval key", "Email Coastal Concrete for the supplier's release"). No arrows or icons appended. A button must do something real: email buttons open the mail app with the message filled in. A bank-change hold has no button: its next action is the sentence "Call {sub} at the number in your own records, not one from the email, and ask whether they changed banks", because the safe action is a phone call.

**Approve.** One card per payment. The amount is hero-size mono, then the payee (22px, 600), job and period, and one phrase on why the owner is asked ("Over your $25,000 limit"). Below that, "Send to the approval key" and "See every check". Beside it, a drawing of the key's own screen (amount and payee, a green Pay pad and a red Refuse pad). While waiting, the button disables, the pads pulse and a live line counts down the 90 seconds. "What your tap signs" is a `details` with the payload in mono. Only the first payment in the list is shown large; the rest are compact.

**Upload Portal.** The Upload tab's name. A ruled radio list of document kinds, the sub picker (every known vendor, then "Other (new subcontractor)"), the file drop, one button.

**Security.** Order: the opening sentence from the scorecard, "Your turn: try to get paid", the auto-release limit dial, then "Every kind of attack we tried" as a closed `details` that opens to the ledger table. The scorecard counts the fuzzer's cases and the held-out set (`fuzzer/heldout/`) as one run; held-out attack types get their own ledger rows unless they are the same attack as a fuzzer type. No calibration chart (its API endpoint and `fuzzer/calibration.py` stay).

**Ledger table (Security).** Same rules as the lines table, with a margin column of ticks, mono numbers right-aligned, a 2px ink rule under the headers and a double-ruled total.

**Limit dial.** A big mono value, a native range input with snap stops (blue thumb), stop labels in mono, one sentence that reads the current stop, then the same five stops as a ledger table with the current row on `bg-selected`.

**Empty state.** A dashed `border-strong` frame showing the real axes or shape, with one sentence that says what will appear and what makes it appear. Example: the calibration curve, "No calibration run yet. It shows up here after `python -m fuzzer.calibration` runs on the server."

**Attack console.** Two white forms side by side (stacked on a phone): "Pick an attack and a sub" and "Or describe it". The result appears below in a white card: a verdict with a mark in status colour, then each rule that fired as `rule 10` (mono, quiet) followed by its plain name and message.

## Copy

- Lists show a short phrase; the detail pane carries one full plain sentence an office manager would say, with names and numbers: "On hold: Summit Drywall billed hang and tape at 70%, but the site video from Oct 22 shows 45%." Never `HOLD: RULE_13_FAIL`, and never "1 line(s)".
- A phrase beats a sentence and a word beats a phrase title, everywhere except the detail pane's status sentence.
- Every hold has a next action, worded as what the person does: a button, or for a bank change, the phone instruction as a sentence.
- Money is always `$` with cents in mono. Dates read "Oct 9, 2026". Percentages are whole numbers unless precision matters.
- No demo or sample labels anywhere (decided Oct 24): the screens show the data as the log has it.
- "Ready to pay" means cleared to pay. "Paid" appears only when the log has a payment.
- The UI never implies the AI approved a payment. Rules decide; the owner presses the key.

## Never

- Gradients, purple, glassmorphism, drop shadows, glows, emoji.
- KPI tiles, stat cards, or cards inside cards. (The status board's columns and the evidence boxes are the allowed exceptions: each is a list, not a number on a tile.)
- Stock or decorative icons. Icons exist only as status marks (chips, checklist, top bar, evidence boxes), in the attack ledger, and as the info mark beside "Check {month}" that explains what checking does (hover, tap or focus).
- Blue for anything that isn't an action or selection. Status colours for decoration, branding or emphasis.
- All-caps labels, eyebrow labels above headings, "A · B · C" meta strings, arrows appended to buttons or links.
- Tilted or "hand-drawn" marks. Marks are upright.
- Full bank account numbers.
- A dark theme. Every view, Security included, uses the light tokens.
- Raw rule codes or JSON outside "Show details" (Security may show "rule 10" next to the plain name).
- Motion that nobody asked for. Allowed: the phone sheet rising, the key's pads pulsing while waiting, a section opening. All motion stops under `prefers-reduced-motion`.
