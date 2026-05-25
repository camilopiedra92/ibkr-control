"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { step3UploadApiSetupStep3UploadPost } from "@/lib/api";
import type { DetectedAccount, Step3UploadResponse } from "@/lib/api";

type FileStatus = "uploading" | "parsed" | "error" | "duplicate";

interface FileEntry {
  name: string;
  status: FileStatus;
  tempId?: string;
  detectedAccounts?: DetectedAccount[];
  newAccounts?: DetectedAccount[];
  error?: string;
}

interface Props {
  onUploaded: (tempIds: string[], newAccounts: DetectedAccount[]) => void;
  onSkip: () => void;
}

/**
 * Step3 Upload — multi-file dropzone for historical Flex XMLs.
 *
 * Each file is POSTed to /api/setup/step3/upload in parallel; the backend
 * parses and stashes the XML in memory (TTL 30min) and returns a temp_id +
 * detected accounts. The "Continuar" button is enabled once at least one
 * file is parsed; the user can also "Saltar" to commit an empty batch and
 * advance to finish.
 *
 * Errors are surfaced per-file; a parse error on one file does not block
 * the others.
 */
export function Step3Upload({ onUploaded, onSkip }: Props) {
  const [files, setFiles] = useState<FileEntry[]>([]);

  async function handleFiles(selected: FileList | null) {
    if (!selected) return;
    const list: FileEntry[] = Array.from(selected).map((f) => ({
      name: f.name,
      status: "uploading",
    }));
    setFiles((prev) => [...prev, ...list]);

    await Promise.all(
      Array.from(selected).map(async (f) => {
        try {
          const r: Step3UploadResponse =
            await step3UploadApiSetupStep3UploadPost({ file: f });
          setFiles((prev) =>
            prev.map((entry) =>
              entry.name === f.name && entry.status === "uploading"
                ? {
                    ...entry,
                    status: "parsed",
                    tempId: r.flex_import_temp_id,
                    detectedAccounts: r.detected_accounts,
                    newAccounts: r.new_accounts,
                  }
                : entry,
            ),
          );
        } catch (e: unknown) {
          const err = e as {
            response?: {
              status: number;
              data?: { detail?: { code?: string } | string };
            };
          };
          const detail = err.response?.data?.detail;
          const code =
            typeof detail === "object" && detail !== null
              ? detail.code
              : undefined;
          const isDuplicate =
            code === "DUPLICATE_XML" || code === "DUPLICATE_XML_STASHED";
          setFiles((prev) =>
            prev.map((entry) =>
              entry.name === f.name && entry.status === "uploading"
                ? {
                    ...entry,
                    status: isDuplicate ? "duplicate" : "error",
                    error: code ?? String(e),
                  }
                : entry,
            ),
          );
        }
      }),
    );
  }

  const parsed = files.filter((f) => f.status === "parsed");
  const allNew = parsed.flatMap((f) => f.newAccounts ?? []);
  const dedupNew = Array.from(
    new Map(allNew.map((a) => [a.ibkr_account_id, a])).values(),
  );

  function advance() {
    onUploaded(
      parsed.map((p) => p.tempId).filter((id): id is string => Boolean(id)),
      dedupNew,
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">XMLs históricos (opcional)</h2>
      <p className="text-sm text-muted-foreground">
        Subí los XMLs de años anteriores si los tenés. Cada uno es procesado
        por separado; un parse error en uno no rompe los demás.
      </p>

      <Input
        type="file"
        accept=".xml"
        multiple
        onChange={(e) => handleFiles(e.target.files)}
      />

      <ul className="space-y-2">
        {files.map((f) => (
          <li
            key={f.name}
            className="text-sm flex justify-between border-b py-1"
          >
            <span className="truncate">{f.name}</span>
            <span
              className={
                f.status === "parsed"
                  ? "text-green-600"
                  : f.status === "duplicate"
                    ? "text-yellow-600"
                    : f.status === "error"
                      ? "text-red-600"
                      : "text-muted-foreground"
              }
            >
              {f.status === "parsed" &&
                `OK${f.newAccounts?.length ? ` (${f.newAccounts.length} new)` : ""}`}
              {f.status === "duplicate" && "ya importado"}
              {f.status === "error" && (f.error ?? "error")}
              {f.status === "uploading" && "subiendo…"}
            </span>
          </li>
        ))}
      </ul>

      <div className="flex justify-between pt-4">
        <Button variant="link" onClick={onSkip}>
          Saltar — no tengo XMLs históricos
        </Button>
        <Button disabled={parsed.length === 0} onClick={advance}>
          Continuar con {parsed.length} XML{parsed.length === 1 ? "" : "s"} →
        </Button>
      </div>
    </div>
  );
}
