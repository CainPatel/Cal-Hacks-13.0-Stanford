# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + Recharts in `web/`, over the FastAPI in `api/`. Deploy: Vercel, with Supabase as the backend store (user decision, Fri Oct 23). Fonts: Geist Sans, Geist Mono (spec).

## Users

The owner or office manager at a 10-30 person commercial general contractor ($5-25M projects), who compiles every subcontractor's monthly pay request into the G702/G703 they sign for the architect and lender. Runs the company from QuickBooks, Excel and a phone; not a software person. Their name is on the request, so a missed problem is their reputation and their money. Secondary: the owner, who presses the approval button for big or unusual payments. Demo audience: hackathon judges, who also open the Security tab.

## Product Purpose

Draw checks every pay request the way the GC checks it by hand (cumulative math, retainage, lien releases, insurance, supplier notices, owner's draw, vendor identity, bank details, duplicates, progress against site video) and says, per request, ready / needs your OK / on hold, in a plain sentence with the evidence and a next action. Success: the office manager approves traces instead of re-checking 40-60 lines per project, and no wrong payment goes out.

## Positioning

The AI never touches money. Claude reads documents and Jev judges, but a deterministic rule kernel decides, and a physical approval key held by the owner signs big or unusual payments. It ships with its own attacker: a fuzzer and a scorecard that say how often it can be robbed.

## Operating Context

Monthly cycle: subs submit by the 25th, GC verifies, owner's draw funds, subs paid by the 10th. Inputs that already exist: G703 spreadsheet or PDF, lien releases, insurance certificates, sub emails, the super's GoPro site video. Zero new data entry: if anyone types into a form, it has failed. Approval key: a Raspberry Pi with green/red buttons on the owner's desk.

## Capabilities and Constraints

Real: rule kernel (15 rules), fuzzer/scorecard, append-only event log, approval-key signing, Claude and Jev calls. Simulated: the GC, its subs, the bank (Nessie sandbox), vendor identities (CRS sandbox), site footage. Not building: daily job reports, chat, multi-agent, landing page, mobile beyond responsive.

## Evidence on Hand

Interview with one small commercial GC (Oct 23): the $1.5M homoglyph wire near-miss, the $70K supplier-lien near-miss, monthly over-billing arguments. Scorecard numbers come from `make fuzz` (data/scorecard.json); never invent figures. No customers, testimonials or pricing exist.

## Product Principles

- Every status is a short phrase in lists and a full sentence an office manager would say in the detail pane; raw codes live behind "Show details".
- Show the document or evidence whenever something is held; every hold has a next action.
- The machine proposes, people and rules decide; the UI never implies the AI approved a payment.
- Nothing typed in: the screen reads what already exists.

## Accessibility & Inclusion

Users in their 50s on a phone or office monitor: big type, big targets, WCAG AA contrast, color never the only signal of status.
