# Draw design system

Source of truth: the approved mockup, `design/mockup.html` (Oct 24, 2026). Tokens live in `web/app/globals.css`; this file says what they mean and how to use them. Where this file and the spec disagree, this file wins (it records decisions made after the spec, such as Security being light).

The idea: the GC's own ledger, checked line by line. Ruled rows, tallied totals, a mark in the margin beside every check, and the proof printed next to every hold. It's a serious tool for an office manager, not a SaaS dashboard.

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

| Status | Text / icon | Soft background | Tailwind |
|---|---|---|---|
| Pass, ready | `#2e6b3a` | `#e2efe4` | `text-pass`, `bg-pass-soft` |
| Hold, needs your OK | `#8a5800` | `#f5e6c3` | `text-hold`, `bg-hold-soft` |
| Fail | `#a3271b` | `#f8e2de` | `text-fail`, `bg-fail-soft` (`--destructive` = fail) |
| Pending, not checked | `#615c54` | `#ebe7e0` | `text-pending`, `bg-pending-soft` |

Every text/background pair above is WCAG AA at body size (amber on its soft background is the tightest, at about 4.9:1).

Note that shadcn's `--accent` is the neutral hover tint, not the blue. The blue is `--primary`.

### What colour means

- **Blue means "you can act here".** It's for primary buttons, links, the focus ring, the selected row's left bar, the active tab underline and the dial's thumb. Nothing else is blue: no blue headings, icons, charts or decoration.
- **Green, amber, red and gray mean status, and only status.** Green: passed or ready. Amber: on hold, or needs the owner's OK. Red: a check failed, or one specific bad thing (the forged letter, an attack that got paid). Gray: pending or not checked.
- **Colour is never the only signal.** A status is always icon plus word (chip), or a mark whose shape differs (tick, cross, ring). Red text is always next to words that say what's wrong.
- **Everything else is ink on stone.** Hierarchy comes from size, weight, rules and space.

## Type

Geist Sans for everything people read. Geist Mono only for money, percentages, account endings, item numbers, file names and the raw trace. Mono numbers always use `tabular-nums` (`.tnum`, or the `.mono`/`.num` pattern) so columns line up to the cent.

| Token | Size / line height | Weight | Used for |
|---|---|---|---|
| `text-meta` | 15px / 1.4 | 400 | Captions, chip text, document field labels, table headers |
| `text-body` | 18px / 1.5 (17px under 720px) | 400 | Body, row sentences, checklist lines |
| `text-say` | 21px / 1.4 | 500 | The status sentence in a detail pane; section headings on Security |
| `text-title` | 24px / 1.25 | 600 | Sub name in the detail pane |
| `text-amount` | 32px / 1.1 | 500 mono | The amount in the detail pane |
| `text-lede` | 34px / 1.25 (26–27px on phone) | 600 | The one sentence at the top of each page |
| `text-hero` | 40–64px fluid / 1.05 | 500 mono | The amount on Approve (and on the key's own screen) |

- Headings use −0.01em to −0.02em tracking. Body uses none.
- Line length stays under about 64ch for prose; the top sentence is capped near 26ch so it reads as a headline.
- Sentence case everywhere.

## Spacing

An 8px base. In Tailwind units: 2 = 8px, 4 = 16px, 6 = 24px, 10 = 40px, 16 = 64px.

- Page gutter: 32px desktop, 16px under 720px. Max content width 1440px.
- Between job sections, and between Security sections: 48–56px.
- Inside a detail pane: 32px padding (16px when it's a phone sheet). Between pane sections: a hairline and 24–32px.
- Rows: 16px vertical, 12px horizontal. Checklist lines: 11px vertical.
- Tap targets: at least 48px tall (buttons 52px, tabs 64px desktop / 52px phone, `summary` 48px).

## Radii

- `rounded-lg` (8px): buttons, chips, panes, cards, the limit dial's inputs.
- 4px: source document frames and the trace block. They're paper, so they're nearly square.
- 14px: only the drawing of the approval key (it's a device).
- Hairline rules have no radius. Nothing is pill-shaped.

## Rules (lines)

- 1px `border` between rows, checklist lines and table rows.
- 2px `foreground` under a job heading and under table headers (the ledger's heading rule).
- 3px double `foreground` under a total: job totals and the "All attacks" total.
- 1px `border-strong` for the vertical margin rule beside checklist marks, input borders and document frames.

## Components

**Top bar.** White sheet, hairline below. "Draw" (24px, 700), the month, and "Sample company, synthetic data" in quiet text. Tabs on the right: quiet text, the active one in ink with a 3px blue underline. On a phone the tabs wrap to their own row and scroll sideways.

**Status chip.** 28px tall, 8px radius, 15px semibold, icon plus word, status colour on its soft background. Ready = tick, On hold = pause bars, Needs your OK = ringed dot. The words are always "Ready", "On hold", "Needs your OK".

**Request row.** Sub name (semibold) and amount (mono, right-aligned, with `$`) on the first line, then chip plus one plain sentence. The whole row is the button. Hover: tint. Selected: `bg-selected` plus a 4px blue bar on the left (inset shadow). Order inside a job: on hold, then needs your OK, then ready; larger amounts first.

**Job section.** Job name (21px, 600) with a 2px ink rule under it, rows with hairlines, then "Total this period" with the total in mono over a double rule.

**Page lede.** One sentence that counts the month ("10 of 15 pay requests are ready to pay. 2 need your OK and 3 are on hold."), then a quiet line with the dollar totals. No KPI tiles.

**Detail pane.** Desktop (≥1100px): a sticky white pane on the right, 6/10 of the width. Under 1100px: a full-screen sheet with "Back to pay requests" at the top; it opens with a 200ms rise and focus moves to the sub's name. Order: sub name, job and period, amount, the status sentence, the next-action button (with a quiet note under it if there's more to do), the evidence, the checklist, the lines table, then "Show details".

**Evidence line beside its source document (the signature).** Every hold shows its proof in two columns (stacking under about 560px): what we found on the left, the document it came from on the right.
- Progress: a 0–100% scale. The site-video value is an ink mark, the billed value an amber mark, the gap between them an amber bar, and the allowed tolerance a hatched band around the video value. Tick labels in mono. One sentence below ("25 points apart. Up to 10 is allowed.").
- Lookalike domain: both domains in mono, one letter per cell, the wrong letter boxed in red. Then a sentence naming the letters, with the letters themselves in mono, because in Geist Sans a capital I and a lowercase l look the same.
- Bank account: `ending 4869 → ending 0724`. Never the full number, anywhere, including inside a quoted email.
- Supplier: a two-line checklist (notice filed, with a tick; release missing, with a cross).
- Source documents are drawn as paper: a white frame with a strong rule, a tinted header with the file name in mono. A G703 line is shown with the form's real column letters (A to I, with G÷C for percent complete) and the disputed cell on the amber soft background. An email shows From, Subject and Sent, then the body; a rush phrase gets a wavy amber underline.

**Checklist with margin marks.** Each check is a row with a 44px margin column, separated by a vertical strong rule. The margin holds one upright mark: a green tick (passed), a red cross (failed), or an amber ring (needs the owner). There's also screen-reader text ("Passed:", "Didn't pass:", "Needs the owner:"). The line text is a bold plain-English name ("Bank details.") followed by the rule's message. Groups: "Needs attention" first, then "Passed". The heading counts them ("15 checks, 12 passed").

**Lines table.** Item (mono), description, billed %, video %, to date. A disputed line gets the amber soft background, with the billed figure in amber.

**Show details.** A `details` element whose summary looks like a link. Inside, the raw kernel trace in 13px mono on tint. This is the only place rule codes appear on the office manager's screens.

**Buttons.** Primary: blue fill, white 18px semibold text, 52px tall, 8px radius. Secondary: blue outline with blue text, transparent fill. Link: blue underlined text. Labels say exactly what happens ("Send to the approval key", "Ask the super to re-verify progress"). No arrows or icons appended.

**Approve.** One card per payment. The amount is hero-size mono, then the payee (22px, 600), job and period, and one sentence on why the owner is asked. Below that, "Send to the approval key" and "See every check". Beside it, a drawing of the key's own screen (amount, payee, job, a green Pay pad and a red Refuse pad). While waiting, the button disables, the pads pulse and a live line counts down the 90 seconds. "What your tap signs" is a `details` with the payload in mono. Only the first payment in the list is shown large; the rest are compact.

**Ledger table (Security).** Same rules as the lines table, with a margin column of ticks, mono numbers right-aligned, a 2px ink rule under the headers and a double-ruled total.

**Limit dial.** A big mono value, a native range input with snap stops (blue thumb), stop labels in mono, one sentence that reads the current stop, then the same five stops as a ledger table with the current row on `bg-selected`.

**Empty state.** A dashed `border-strong` frame showing the real axes or shape, with one sentence that says what will appear and what makes it appear. Example: the calibration curve, "No calibration run yet. It shows up here after `python -m fuzzer.calibration` runs on the server."

**Attack console.** Two white forms side by side (stacked on a phone): "Pick an attack and a sub" and "Or describe it". The result appears below in a white card: a verdict sentence in status colour, then each rule that fired as `rule 10` (mono, quiet) followed by its plain name and message.

## Copy

- Every status is a sentence an office manager would say, with names and numbers: "On hold: Summit Drywall billed hang and tape at 70%, but the site video from Oct 22 shows 45%." Never `HOLD: RULE_13_FAIL`, and never "1 line(s)".
- Every hold has a next action as a button, worded as what the person does.
- Money is always `$` with cents in mono. Dates read "Oct 9, 2026". Percentages are whole numbers unless precision matters.
- Synthetic data is labelled ("Sample company, synthetic data").
- The UI never implies the AI approved a payment. Rules decide; the owner presses the key.

## Never

- Gradients, purple, glassmorphism, drop shadows, glows, emoji.
- KPI tiles, stat cards, card grids, cards inside cards.
- Stock or decorative icons. Icons exist only inside chips, checklist marks and the attack ledger.
- Blue for anything that isn't an action or selection. Status colours for decoration, branding or emphasis.
- All-caps labels, eyebrow labels above headings, "A · B · C" meta strings, arrows appended to buttons or links.
- Tilted or "hand-drawn" marks. Marks are upright.
- Full bank account numbers.
- A dark theme. Every view, Security included, uses the light tokens.
- Raw rule codes or JSON outside "Show details" (Security may show "rule 10" next to the plain name).
- Motion that nobody asked for. Allowed: the phone sheet rising, the key's pads pulsing while waiting. All motion stops under `prefers-reduced-motion`.
