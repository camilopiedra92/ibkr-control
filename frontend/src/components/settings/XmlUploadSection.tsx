"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { uploadXmlApiImportsUploadPost } from "@/lib/api";

interface FileStatus {
  name: string;
  state: "pending" | "uploading" | "ok" | "duplicate" | "error";
  message?: string;
}

function statusIcon(state: FileStatus["state"]): string {
  switch (state) {
    case "ok":
      return "✓";
    case "duplicate":
      return "⚠";
    case "error":
      return "✗";
    case "uploading":
      return "⏳";
    default:
      return "⏸";
  }
}

function statusColor(state: FileStatus["state"]): string {
  switch (state) {
    case "ok":
      return "text-green-600";
    case "duplicate":
      return "text-yellow-600";
    case "error":
      return "text-red-600";
    case "uploading":
      return "text-blue-600";
    default:
      return "text-muted-foreground";
  }
}

export function XmlUploadSection() {
  const [files, setFiles] = useState<FileStatus[]>([]);
  const [isUploading, setIsUploading] = useState(false);

  const uploadFile = useCallback(async (file: File): Promise<void> => {
    setFiles((prev) =>
      prev.map((f) => (f.name === file.name ? { ...f, state: "uploading" } : f))
    );

    try {
      const result = await uploadXmlApiImportsUploadPost({ file });
      const detail = result as Record<string, unknown>;
      const isDuplicate =
        detail?.duplicate === true || detail?.status === "duplicate";

      setFiles((prev) =>
        prev.map((f) =>
          f.name === file.name
            ? {
                ...f,
                state: isDuplicate ? "duplicate" : "ok",
                message: isDuplicate ? "Ya existia (ignorado)" : "Importado",
              }
            : f
        )
      );
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      const msg = e?.response?.data?.detail ?? "Error al subir";
      setFiles((prev) =>
        prev.map((f) =>
          f.name === file.name ? { ...f, state: "error", message: msg } : f
        )
      );
    }
  }, []);

  const onDrop = useCallback(
    async (accepted: File[]) => {
      if (isUploading) return;

      const newFiles = accepted.filter(
        (f) => !files.some((existing) => existing.name === f.name)
      );
      if (newFiles.length === 0) return;

      setFiles((prev) => [
        ...prev,
        ...newFiles.map((f) => ({ name: f.name, state: "pending" as const })),
      ]);

      setIsUploading(true);
      try {
        for (const file of newFiles) {
          await uploadFile(file);
        }
      } finally {
        setIsUploading(false);
      }
    },
    [files, isUploading, uploadFile]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "text/xml": [".xml"], "application/xml": [".xml"] },
    multiple: true,
    disabled: isUploading,
  });

  function handleClear() {
    setFiles([]);
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Importar XMLs historicos</h2>
        {files.length > 0 && !isUploading && (
          <button
            type="button"
            onClick={handleClear}
            className="text-xs text-muted-foreground hover:text-foreground underline"
          >
            Limpiar lista
          </button>
        )}
      </div>

      <p className="text-sm text-muted-foreground">
        Subi XMLs Flex de anos anteriores para completar el historial. Los
        duplicados se detectan automaticamente y se ignoran.
      </p>

      <div
        {...getRootProps()}
        className={[
          "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors",
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-border hover:border-primary/50",
          isUploading ? "opacity-50 cursor-not-allowed" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <input {...getInputProps()} />
        <p className="text-2xl mb-2">↑</p>
        <p className="text-sm font-medium">
          {isDragActive
            ? "Solta los archivos aqui"
            : "Arrastra XMLs aqui o hace clic para seleccionar"}
        </p>
        <p className="text-xs text-muted-foreground mt-1">Solo archivos .xml</p>
      </div>

      {files.length > 0 && (
        <ul className="space-y-1 text-sm">
          {files.map((f) => (
            <li key={f.name} className="flex items-center gap-2">
              <span className={`font-mono text-base ${statusColor(f.state)}`}>
                {statusIcon(f.state)}
              </span>
              <span className="flex-1 truncate font-mono text-xs">{f.name}</span>
              {f.message && (
                <span className={`text-xs ${statusColor(f.state)}`}>
                  {f.message}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
