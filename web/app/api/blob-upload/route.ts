import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { connection } from "next/server";
import { timingSafeEqual } from "node:crypto";

// Issues short-lived tokens so the browser can upload straight to the private Blob store
// (skipping Vercel's 4.5 MB function limit, which site video needs). The dashboard has no
// logins, so a passcode set only on the server (UPLOAD_PASSCODE) stands between the public
// internet and the store. Deployed without one, uploads stay off.

const TYPES: Record<string, string[]> = {
  pay_app: ["application/pdf"],
  release: ["application/pdf"],
  coi: ["application/pdf"],
  email: ["message/rfc822", "application/octet-stream", "text/plain"],
  site_video: ["video/*"],
};
const MB = 1024 * 1024;

function passcodeOk(given: unknown): boolean {
  const want = process.env.UPLOAD_PASSCODE;
  if (!want) return !process.env.VERCEL; // local development only
  const a = Buffer.from(typeof given === "string" ? given : "");
  const b = Buffer.from(want);
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function GET() {
  await connection(); // reads env per request: never prerendered at build time
  return Response.json({
    enabled: Boolean(process.env.BLOB_READ_WRITE_TOKEN),
    passcode: Boolean(process.env.UPLOAD_PASSCODE || process.env.VERCEL),
  });
}

export async function POST(request: Request) {
  const body = (await request.json()) as HandleUploadBody;
  try {
    const json = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname, clientPayload) => {
        const { passcode, kind } = JSON.parse(clientPayload ?? "{}") as { passcode?: string; kind?: string };
        if (process.env.VERCEL && !process.env.UPLOAD_PASSCODE) throw new Error("Uploads are off until UPLOAD_PASSCODE is set.");
        if (!passcodeOk(passcode)) throw new Error("That upload passcode isn't right.");
        if (!kind || !(kind in TYPES)) throw new Error("Unknown document type.");
        if (!pathname.startsWith("uploads/")) throw new Error("Uploads go under uploads/.");
        return {
          allowedContentTypes: TYPES[kind],
          maximumSizeInBytes: kind === "site_video" ? 500 * MB : 32 * MB,
          addRandomSuffix: true,
        };
      },
    });
    return Response.json(json);
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 400 });
  }
}
