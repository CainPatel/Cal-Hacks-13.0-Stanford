"use client";

import { upload } from "@vercel/blob/client";
import { CircleCheck, CircleX, FileUp, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, ApiError, money, type DocKind, type IngestResult, type RequestRow } from "@/lib/api";
import { cn } from "@/lib/utils";

const KINDS: { kind: DocKind; label: string; hint: string; accept: string; needs: "sub" | "job" | "none" }[] = [
  { kind: "pay_app", label: "Pay application", hint: "The sub's G702/G703, as a PDF.", accept: "application/pdf,.pdf", needs: "sub" },
  { kind: "release", label: "Lien release", hint: "The signed conditional waiver for this period.", accept: "application/pdf,.pdf", needs: "sub" },
  { kind: "coi", label: "Insurance certificate", hint: "Their current COI.", accept: "application/pdf,.pdf", needs: "sub" },
  { kind: "email", label: "Email", hint: "Save the email as a file (.eml) and drop it here.", accept: ".eml,message/rfc822", needs: "none" },
  { kind: "site_video", label: "Site video", hint: "The super's walkthrough. Up to 500 MB.", accept: "video/*", needs: "job" },
];

type Phase = { name: "idle" } | { name: "uploading"; pct: number } | { name: "reading" } | { name: "done"; result: IngestResult } | { name: "error"; message: string };

export default function UploadPage() {
  const [kind, setKind] = useState<DocKind>("pay_app");
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [subId, setSubId] = useState("");
  const [jobId, setJobId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [passcode, setPasscode] = useState("");
  const [cfg, setCfg] = useState<{ enabled: boolean; passcode: boolean }>({ enabled: false, passcode: false });
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.requests(6).then((r) => {
      setRows(r);
      setSubId(r[0]?.sub_id ?? "");
      setJobId(r[0]?.job_id ?? "");
    }, () => {});
    fetch("/api/blob-upload").then((r) => r.json()).then(setCfg, () => {});
  }, []);

  const spec = KINDS.find((k) => k.kind === kind)!;
  const subs = useMemo(() => [...new Map(rows.map((r) => [r.sub_id, r])).values()].sort((a, b) => a.sub.localeCompare(b.sub)), [rows]);
  const jobs = useMemo(() => [...new Map(rows.map((r) => [r.job_id, r.job])).entries()], [rows]);
  const sub = subs.find((s) => s.sub_id === subId);
  const ids = spec.needs === "sub" ? { sub_id: subId, job_id: sub?.job_id } : spec.needs === "job" ? { job_id: jobId } : {};
  const busy = phase.name === "uploading" || phase.name === "reading";

  const send = async () => {
    if (!file) return;
    try {
      let result: IngestResult;
      if (cfg.enabled) {
        setPhase({ name: "uploading", pct: 0 });
        const blob = await upload(`uploads/${kind}/${file.name}`, file, {
          access: "private",
          handleUploadUrl: "/api/blob-upload",
          clientPayload: JSON.stringify({ passcode, kind }),
          multipart: file.size > 20 * 1024 * 1024,
          onUploadProgress: (e) => setPhase({ name: "uploading", pct: Math.round(e.percentage) }),
        });
        setPhase({ name: "reading" });
        result = await api.ingest(kind, blob.pathname, ids);
      } else {
        setPhase({ name: "reading" });
        result = await api.uploadDirect(kind, file, ids);
      }
      setPhase({ name: "done", result });
      setFile(null);
    } catch (e) {
      setPhase({ name: "error", message: e instanceof ApiError ? e.message : (e as Error).message });
    }
  };

  return (
    <main className="mx-auto w-full max-w-[760px] flex-1 space-y-8 px-4 py-6 sm:px-6 lg:py-10">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Add what the sub sent</h1>
        <p className="max-w-prose text-[17px] leading-snug text-muted-foreground">
          Drop in the document as it came. Draw reads it and checks it with everything else; there&apos;s nothing to type.
        </p>
      </header>

      <form className="space-y-7" onSubmit={(e) => (e.preventDefault(), send())}>
        <fieldset className="space-y-3">
          <legend className="text-lg font-semibold">What is it?</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {KINDS.map((k) => (
              <label
                key={k.kind}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border bg-sheet px-4 py-3 transition-colors hover:bg-muted/60",
                  kind === k.kind && "border-primary bg-accent hover:bg-accent",
                )}
              >
                <input type="radio" name="kind" value={k.kind} checked={kind === k.kind} onChange={() => (setKind(k.kind), setFile(null))} className="mt-1 size-4 accent-[var(--primary)]" />
                <span>
                  <span className="block text-[17px] font-medium">{k.label}</span>
                  <span className="block text-[15px] leading-snug text-muted-foreground">{k.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {spec.needs === "sub" && (
          <label className="block space-y-1.5">
            <span className="text-lg font-semibold">Who sent it?</span>
            <select value={subId} onChange={(e) => setSubId(e.target.value)} className="h-12 w-full rounded-md border bg-sheet px-3 text-[17px]">
              {subs.map((s) => (
                <option key={s.sub_id} value={s.sub_id}>
                  {s.sub} ({s.job})
                </option>
              ))}
            </select>
          </label>
        )}
        {spec.needs === "job" && (
          <label className="block space-y-1.5">
            <span className="text-lg font-semibold">Which job?</span>
            <select value={jobId} onChange={(e) => setJobId(e.target.value)} className="h-12 w-full rounded-md border bg-sheet px-3 text-[17px]">
              {jobs.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="space-y-1.5">
          <span className="text-lg font-semibold">The file</span>
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
              "flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed bg-sheet px-4 py-6 text-center transition-colors hover:bg-muted/50",
              dragging && "border-primary bg-accent",
            )}
          >
            <FileUp className="size-7 text-muted-foreground" aria-hidden />
            {file ? (
              <span className="text-[17px]">
                <b className="font-medium">{file.name}</b> <span className="text-muted-foreground">({(file.size / 1024 / 1024).toFixed(1)} MB)</span>
              </span>
            ) : (
              <span className="text-[17px] text-muted-foreground">Drop it here, or click to choose</span>
            )}
            <input ref={input} type="file" accept={spec.accept} className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        </div>

        {cfg.enabled && cfg.passcode && (
          <label className="block space-y-1.5">
            <span className="text-lg font-semibold">Upload passcode</span>
            <input type="password" autoComplete="off" value={passcode} onChange={(e) => setPasscode(e.target.value)} className="h-12 w-full rounded-md border bg-sheet px-3 text-[17px]" />
            <span className="block text-[15px] text-muted-foreground">Ask whoever set up Draw. It keeps strangers from uploading.</span>
          </label>
        )}

        <Button size="lg" type="submit" disabled={!file || busy || (spec.needs === "sub" && !subId)}>
          {busy ? <LoaderCircle className="animate-spin" aria-hidden /> : <FileUp aria-hidden />}
          {phase.name === "uploading" ? `Uploading… ${phase.pct}%` : phase.name === "reading" ? "Reading it…" : `Add ${spec.label.toLowerCase()}`}
        </Button>
      </form>

      <div aria-live="polite">
        {phase.name === "error" && (
          <p className="flex items-start gap-2 rounded-lg border border-fail/30 bg-fail-soft p-4 text-[17px] text-fail">
            <CircleX className="mt-0.5 size-5 shrink-0" aria-hidden /> {phase.message}
          </p>
        )}
        {phase.name === "done" && <Result result={phase.result} sub={sub?.sub} />}
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

function Result({ result, sub }: { result: IngestResult; sub?: string }) {
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
    <div className="space-y-3 rounded-lg border bg-sheet p-5">
      <p className="flex items-start gap-2 text-[17px] font-medium text-pass">
        <CircleCheck className="mt-0.5 size-5 shrink-0" aria-hidden /> {what}
      </p>
      {result.method && (
        <p className="text-[15px] text-muted-foreground">
          {result.method === "text_layer" ? "Read straight from the PDF's text." : "The PDF had no readable text, so Claude transcribed it."}
          {result.fallback_reason ? ` (${result.fallback_reason})` : ""}
        </p>
      )}
      {result.notes && result.notes.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-[15px]">
          {result.notes.map((n, i) => (
            <li key={i}>{n.message}</li>
          ))}
        </ul>
      )}
      {result.attachments && result.attachments.length > 0 && (
        <ul className="space-y-1.5 text-[15px]">
          {result.attachments.map((a, i) => (
            <li key={i} className="flex items-start gap-2">
              {a.status === "recorded" || a.status === "used" ? (
                <CircleCheck className="mt-0.5 size-4 shrink-0 text-pass" aria-hidden />
              ) : (
                <CircleX className="mt-0.5 size-4 shrink-0 text-hold" aria-hidden />
              )}
              <span>
                <b className="font-medium">{a.filename}</b>: {a.status === "used" ? a.detail : ATTACHMENT_SAYS[a.status]}
                {a.detail && a.status !== "used" ? ` ${a.detail}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {result.request_id &&
        (checked ? (
          <Link href={`/?r=${result.request_id}`} className="text-[17px] text-primary underline">
            See the checks
          </Link>
        ) : (
          <Button
            size="lg"
            disabled={checking}
            onClick={async () => {
              setChecking(true);
              await api.replay(6).catch(() => {});
              setChecking(false);
              setChecked(true);
            }}
          >
            {checking && <LoaderCircle className="animate-spin" aria-hidden />} Check it now
          </Button>
        ))}
    </div>
  );
}
