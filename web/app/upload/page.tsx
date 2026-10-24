"use client";

import { upload } from "@vercel/blob/client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Mark } from "@/components/evidence";
import { Button } from "@/components/ui/button";
import { api, ApiError, money, type DocKind, type IngestResult, type VendorList } from "@/lib/api";
import { cn } from "@/lib/utils";

const KINDS: { kind: DocKind; label: string; hint: string; accept: string; needs: "sub" | "job" | "none" }[] = [
  { kind: "pay_app", label: "Pay application", hint: "The sub's G702/G703, as a PDF.", accept: "application/pdf,.pdf", needs: "sub" },
  { kind: "release", label: "Lien release", hint: "The signed conditional waiver for this period.", accept: "application/pdf,.pdf", needs: "sub" },
  { kind: "coi", label: "Insurance certificate", hint: "Their current COI.", accept: "application/pdf,.pdf", needs: "sub" },
  { kind: "email", label: "Email", hint: "Save the email as a file (.eml) and drop it here.", accept: ".eml,message/rfc822", needs: "none" },
  { kind: "site_video", label: "Site video", hint: "The super's walkthrough. Up to 500 MB.", accept: "video/*", needs: "job" },
];
const NEW = "new"; // the "Other (new subcontractor)" choice

const FOCUS = "outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
const FIELD = cn("min-h-12 w-full rounded-md border border-border-strong bg-background px-3 py-2.5 text-[17px]", FOCUS);
const LABEL = "block text-body font-semibold";
const HINT = "mt-1 block text-meta text-muted-foreground";
const BUTTON = "h-13 px-6 text-lg font-semibold";

type Phase = { name: "idle" } | { name: "uploading"; pct: number } | { name: "reading" } | { name: "done"; result: IngestResult; sub?: string; isNew: boolean } | { name: "error"; message: string };

export default function UploadPage() {
  return (
    <Suspense>
      <UploadForm />
    </Suspense>
  );
}

function UploadForm() {
  const params = useSearchParams();
  const [kind, setKind] = useState<DocKind>(() => (KINDS.some((k) => k.kind === params.get("kind")) ? (params.get("kind") as DocKind) : "pay_app"));
  const [list, setList] = useState<VendorList>({ vendors: [], jobs: [] });
  const [subId, setSubId] = useState("");
  const [jobId, setJobId] = useState("");
  const [fresh, setFresh] = useState({ legal_name: "", email_domain: "", ein: "", bank_account: "" });
  const [file, setFile] = useState<File | null>(null);
  const [passcode, setPasscode] = useState("");
  const [cfg, setCfg] = useState<{ enabled: boolean; passcode: boolean }>({ enabled: false, passcode: false });
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = (select?: string) =>
    api.vendors().then((l) => {
      setList(l);
      const pick = select ?? params.get("sub") ?? "";
      setSubId(l.vendors.some((v) => v.id === pick) ? pick : (l.vendors[0]?.id ?? ""));
      setJobId((j) => j || (l.vendors.find((v) => v.id === pick)?.job_ids[0] ?? l.jobs[0]?.id ?? ""));
    }, () => {});

  useEffect(() => {
    load();
    fetch("/api/blob-upload").then((r) => r.json()).then(setCfg, () => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once on load; `?sub=` is read inside
  }, []);

  const spec = KINDS.find((k) => k.kind === kind)!;
  const isNew = spec.kind === "pay_app" && subId === NEW;
  const sub = list.vendors.find((v) => v.id === subId);
  // an existing sub bills on its own job; a picker only when it has several, or none yet
  const subJobs = sub ? (sub.job_ids.length ? list.jobs.filter((j) => sub.job_ids.includes(j.id)) : list.jobs) : list.jobs;
  const subJob = sub?.job_ids.length === 1 ? sub.job_ids[0] : subJobs.some((j) => j.id === jobId) ? jobId : subJobs[0]?.id;
  const needsJobPicker = spec.needs === "job" || isNew || (spec.needs === "sub" && sub !== undefined && sub.job_ids.length !== 1);
  const busy = phase.name === "uploading" || phase.name === "reading";

  const problems = (): string[] => {
    if (!isNew) return [];
    const name = fresh.legal_name.trim().replace(/\s+/g, " ");
    const out = [];
    const same = list.vendors.find((v) => v.legal_name.trim().toLowerCase() === name.toLowerCase());
    if (!name) out.push("Enter the new subcontractor's legal name.");
    else if (same) out.push(`${same.legal_name} is already a subcontractor. Pick them from the list instead.`);
    if (!jobId) out.push("Choose the job they're billing on.");
    return out;
  };

  const send = async () => {
    if (!file) return;
    const issues = problems();
    if (issues.length) return setPhase({ name: "error", message: issues.join(" ") });
    let created: { id: string; legal_name: string } | null = null;
    try {
      let pathname: string | null = null;
      if (cfg.enabled) {
        // the file goes to the private store before anyone is created, so a failed upload leaves no new sub behind
        setPhase({ name: "uploading", pct: 0 });
        const blob = await upload(`uploads/${kind}/${file.name}`, file, {
          access: "private",
          handleUploadUrl: "/api/blob-upload",
          clientPayload: JSON.stringify({ passcode, kind }),
          multipart: file.size > 20 * 1024 * 1024,
          onUploadProgress: (e) => setPhase({ name: "uploading", pct: Math.round(e.percentage) }),
        });
        pathname = blob.pathname;
      }
      setPhase({ name: "reading" });
      if (isNew) {
        created = await api.createVendor(fresh, passcode);
        await load(created.id); // a retry after a failed read uses this sub; it can't be created twice
      }
      const sid = created?.id ?? subId;
      const ids = spec.needs === "sub" ? { sub_id: sid, job_id: isNew ? jobId : subJob } : spec.needs === "job" ? { job_id: jobId } : {};
      const result = pathname ? await api.ingest(kind, pathname, ids) : await api.uploadDirect(kind, file, ids);
      setPhase({ name: "done", result, sub: created?.legal_name ?? sub?.legal_name, isNew: Boolean(created) });
      setFile(null);
      if (created) {
        setFresh({ legal_name: "", email_domain: "", ein: "", bank_account: "" });
        load(created.id); // now with the job their pay app put them on
      }
    } catch (e) {
      const message = e instanceof ApiError ? e.message : (e as Error).message;
      setPhase({
        name: "error",
        message: created
          ? `${created.legal_name} was added, but the pay application couldn't be read: ${message} Try the file again; they're in the list now.`
          : message,
      });
    }
  };

  const optional = (key: "email_domain" | "ein" | "bank_account", label: string, mode?: "numeric") => (
    <label className="block">
      <span className={LABEL}>
        {label} <span className="font-normal text-muted-foreground">(if you have it)</span>
      </span>
      <input
        value={fresh[key]}
        onChange={(e) => setFresh((f) => ({ ...f, [key]: e.target.value }))}
        inputMode={mode}
        autoComplete="off"
        className={cn(FIELD, "mt-1.5")}
      />
    </label>
  );

  const jobPicker = (
    <label className="block">
      <span className={LABEL}>{isNew ? "Which job are they billing on?" : "Which job?"}</span>
      <select
        value={spec.needs === "sub" && !isNew ? subJob : jobId}
        onChange={(e) => setJobId(e.target.value)}
        required
        className={cn(FIELD, "mt-1.5")}
      >
        {(isNew || spec.needs === "job" ? list.jobs : subJobs).map((j) => (
          <option key={j.id} value={j.id}>
            {j.name}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <main className="mx-auto w-full max-w-[760px] flex-1 px-4 py-6 sm:px-8 lg:py-10">
      <h1 className="text-[27px] leading-tight font-semibold tracking-[-0.02em] sm:text-lede">Add what the sub sent</h1>
      <p className="mt-3 max-w-[60ch] text-body text-muted-foreground">
        Drop in the document as it came. Draw reads it and checks it with everything else; there&apos;s nothing to type.
      </p>

      <form className="mt-8 space-y-8" onSubmit={(e) => (e.preventDefault(), send())}>
        <fieldset>
          <legend className="text-[22px] font-semibold tracking-[-0.01em]">What is it?</legend>
          <div className="mt-3 border-t-2 border-foreground">
            {KINDS.map((k) => (
              <label
                key={k.kind}
                className={cn("flex min-h-16 cursor-pointer items-start gap-3.5 border-b px-3 py-3.5 hover:bg-muted", kind === k.kind && "bg-selected shadow-[inset_4px_0_0_var(--primary)] hover:bg-selected")}
              >
                <input
                  type="radio"
                  name="kind"
                  value={k.kind}
                  checked={kind === k.kind}
                  onChange={() => (setKind(k.kind), setFile(null), subId === NEW && k.kind !== "pay_app" && setSubId(list.vendors[0]?.id ?? ""))}
                  className={cn("mt-1 size-5 shrink-0 rounded-full accent-primary", FOCUS)}
                />
                <span>
                  <span className="block text-body font-medium">{k.label}</span>
                  <span className="block text-meta text-muted-foreground">{k.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {spec.needs === "sub" && (
          <label className="block">
            <span className={LABEL}>Who sent it?</span>
            <select value={subId} onChange={(e) => setSubId(e.target.value)} className={cn(FIELD, "mt-1.5")}>
              {list.vendors.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.legal_name}
                </option>
              ))}
              {spec.kind === "pay_app" && <option value={NEW}>Other (new subcontractor)</option>}
            </select>
            {spec.kind !== "pay_app" && <span className={HINT}>A new subcontractor starts with their pay application.</span>}
          </label>
        )}

        {isNew && (
          <fieldset className="space-y-5 border-l-2 border-border-strong pl-4 sm:pl-5">
            <legend className="mb-3 text-body text-muted-foreground">The new subcontractor. Their first payment will need the owner&apos;s OK.</legend>
            <label className="block">
              <span className={LABEL}>Legal name</span>
              <input
                value={fresh.legal_name}
                onChange={(e) => setFresh((f) => ({ ...f, legal_name: e.target.value }))}
                required
                autoComplete="off"
                className={cn(FIELD, "mt-1.5")}
              />
            </label>
            {jobPicker}
            {optional("email_domain", "Email domain")}
            {optional("ein", "EIN", "numeric")}
            {optional("bank_account", "Bank account")}
          </fieldset>
        )}

        {needsJobPicker && !isNew && jobPicker}

        <div>
          <span className={LABEL}>The file</span>
          <label
            onDragOver={(e) => (e.preventDefault(), setDragging(true))}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const f = e.dataTransfer.files[0];
              if (f) setFile(f);
            }}
            className={cn(
              "mt-1.5 flex min-h-36 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border-strong bg-sheet px-4 py-6 text-center hover:bg-muted has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring/50",
              dragging && "border-primary bg-selected",
            )}
          >
            {file ? (
              <>
                <span className="font-mono text-[15px] [overflow-wrap:anywhere]">{file.name}</span>
                <span className="text-meta text-muted-foreground">
                  <span className="font-mono tnum">{(file.size / 1024 / 1024).toFixed(1)}</span> MB. Choose another to replace it.
                </span>
              </>
            ) : (
              <span className="text-body text-muted-foreground">Drop it here, or choose a file</span>
            )}
            <input ref={input} type="file" accept={spec.accept} className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        </div>

        {cfg.enabled && cfg.passcode && (
          <label className="block">
            <span className={LABEL}>Upload passcode</span>
            <input type="password" autoComplete="off" value={passcode} onChange={(e) => setPasscode(e.target.value)} className={cn(FIELD, "mt-1.5")} />
            <span className={HINT}>Ask whoever set up Draw. It keeps strangers from uploading.</span>
          </label>
        )}

        <Button className={BUTTON} type="submit" disabled={!file || busy || (spec.needs === "sub" && !subId)}>
          {phase.name === "uploading" ? `Uploading… ${phase.pct}%` : phase.name === "reading" ? "Reading it…" : `Add ${spec.label.toLowerCase()}`}
        </Button>
      </form>

      <div aria-live="polite" className="mt-6">
        {phase.name === "error" && (
          <p className="flex items-start gap-2 rounded-lg border border-fail/30 bg-fail-soft p-4 text-body text-fail">
            <Mark kind="fail" />
            <span>{phase.message}</span>
          </p>
        )}
        {phase.name === "done" && <Result result={phase.result} sub={phase.sub} isNew={phase.isNew} />}
      </div>
    </main>
  );
}

const ATTACHMENT_SAYS: Record<NonNullable<IngestResult["attachments"]>[number]["status"], string> = {
  recorded: "filed.",
  used: "read with the pay application.",
  needs_person: "needs you to look at it.",
  duplicate: "already on file.",
  unreadable: "couldn't be read.",
};

function Result({ result, sub, isNew }: { result: IngestResult; sub?: string; isNew: boolean }) {
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const pr = result.pay_request;
  const what = result.request_id
    ? `Read ${sub ?? "the sub"}'s pay application #${pr?.number}: ${pr ? money(pr.g702.current_payment_due) : ""} due.`
    : result.release_id
      ? `Recorded the lien release from ${sub ?? "the sub"}.`
      : result.coi_id
        ? `Recorded ${sub ?? "the sub"}'s insurance certificate.`
        : result.email_id
          ? result.attachments?.length
            ? `Recorded the email and its ${result.attachments.length} PDF${result.attachments.length === 1 ? "" : "s"}.`
            : "Recorded the email."
          : "Read the site video.";
  return (
    <div className="rounded-lg border bg-sheet p-5">
      <p className="flex items-start gap-2 text-say font-medium text-pass">
        <Mark kind="pass" />
        <span>{what}</span>
      </p>
      {isNew && <p className="mt-2 text-body">{sub} is a new subcontractor, so their first payment will need the owner&apos;s OK.</p>}
      {result.method && (
        <p className="mt-2 text-meta text-muted-foreground">
          {result.method === "text_layer" ? "Read straight from the PDF's text." : "The PDF had no readable text, so Claude transcribed it."}
          {result.fallback_reason ? ` (${result.fallback_reason})` : ""}
        </p>
      )}
      {result.notes && result.notes.length > 0 && (
        <ul className="mt-3 border-t">
          {result.notes.map((n, i) => (
            <li key={i} className="border-b py-2 text-body">
              {n.message}
            </li>
          ))}
        </ul>
      )}
      {result.attachments && result.attachments.length > 0 && (
        <ul className="mt-3 border-t">
          {result.attachments.map((a, i) => (
            <li key={i} className="flex items-start gap-2 border-b py-2 text-body">
              <Mark kind={a.status === "recorded" || a.status === "used" ? "pass" : "ok"} />
              <span>
                <span className="font-mono text-[15px]">{a.filename}</span>: {a.status === "used" ? a.detail : ATTACHMENT_SAYS[a.status]}
                {a.detail && a.status !== "used" ? ` ${a.detail}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {result.request_id && (
        <div className="mt-4">
          {checked ? (
            <Link href={`/?r=${result.request_id}`} className={cn("rounded-sm text-body text-primary underline underline-offset-[3px]", FOCUS)}>
              See the checks
            </Link>
          ) : (
            <Button
              className={BUTTON}
              disabled={checking}
              onClick={async () => {
                setChecking(true);
                await api.replay(6).catch(() => {});
                setChecking(false);
                setChecked(true);
              }}
            >
              {checking ? "Checking…" : "Check it now"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
