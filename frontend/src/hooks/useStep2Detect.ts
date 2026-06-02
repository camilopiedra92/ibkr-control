import { useCallback, useEffect, useRef, useState } from "react";
import {
  step2DetectApiSetupStep2DetectPost,
  step2DetectFromXmlApiSetupStep2DetectFromXmlPost,
} from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

/**
 * Discriminated union of every error the Step2 detect flow can surface to the
 * UI. The backend maps Flex client errors to specific HTTP status codes
 * (see backend/src/ibkr_control/api/setup.py step2_detect):
 *
 *   503  IBKR_BUSY      — Flex 1001 after 4 retries (5s/15s/30s backoff)
 *   401  INVALID_TOKEN  — Flex 1003/1004/1018
 *   400  QUERY_NOT_FOUND — Flex 1005
 *   504  IBKR_TIMEOUT   — poll exceeded deadline (mapped to TIMEOUT here)
 *   502  IBKR_ERROR     — generic Flex client error with code + message
 *   *    UNKNOWN        — network failure, parse error, anything else
 */
export type DetectError =
  | { code: "IBKR_BUSY"; attempts: number }
  | { code: "INVALID_TOKEN" }
  | { code: "QUERY_NOT_FOUND" }
  | { code: "IBKR_ERROR"; message: string }
  | { code: "TIMEOUT" }
  | { code: "UNKNOWN"; message: string };

export interface UseStep2DetectResult {
  /** Triggers POST /api/setup/step2/detect. Returns the detected accounts on success, null on error. */
  detect: () => Promise<DetectedAccount[] | null>;
  /** Triggers POST /api/setup/step2/detect_from_xml as a fallback when IBKR is offline. */
  detectFromXml: (file: File) => Promise<DetectedAccount[] | null>;
  /** True while either request is in flight. */
  isLoading: boolean;
  /** Seconds elapsed since the in-flight request started — drives the retry visualization. */
  elapsedSeconds: number;
  /** The most recent error, or null. */
  error: DetectError | null;
}

/**
 * Minimal axios error shape — we only need `response.status` and `response.data.detail`.
 * Defined locally to avoid importing axios types in a hook that does not
 * otherwise depend on axios internals.
 */
interface AxiosErrorLike {
  response?: {
    status: number;
    data?: { detail?: unknown };
  };
}

function isAxiosErrorLike(e: unknown): e is AxiosErrorLike {
  return (
    typeof e === "object" &&
    e !== null &&
    "response" in e &&
    typeof (e as { response?: unknown }).response === "object"
  );
}

function mapAxiosErrorToDetectError(e: unknown): DetectError {
  if (!isAxiosErrorLike(e) || !e.response) {
    return { code: "UNKNOWN", message: String(e) };
  }
  const { status, data } = e.response;
  const detail = data?.detail;

  if (
    status === 503 &&
    typeof detail === "object" &&
    detail !== null &&
    "code" in detail &&
    (detail).code === "IBKR_BUSY"
  ) {
    const attempts = Number(
      (detail as { attempts?: unknown }).attempts ?? 0,
    );
    return { code: "IBKR_BUSY", attempts };
  }
  if (status === 401) return { code: "INVALID_TOKEN" };
  if (status === 400) return { code: "QUERY_NOT_FOUND" };
  if (status === 504) return { code: "TIMEOUT" };
  if (status === 502) {
    const message =
      typeof detail === "string" ? detail : JSON.stringify(detail ?? "");
    return { code: "IBKR_ERROR", message };
  }
  return { code: "UNKNOWN", message: `Error inesperado (HTTP ${status})` };
}

/**
 * Hook for the Step 2 "detect accounts" flow.
 *
 * Wraps the Orval-generated POST helpers (success path returns the unwrapped
 * body thanks to the axios mutator interceptor) while preserving structured
 * error detail on the failure path — axios still attaches `.response` to the
 * thrown error so we can read `status` + `data.detail` to build the typed
 * DetectError union the UI consumes.
 */
export function useStep2Detect(): UseStep2DetectResult {
  const [isLoading, setIsLoading] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [error, setError] = useState<DetectError | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Safety net: clear the tick interval if the component unmounts mid-request.
  useEffect(() => {
    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, []);

  const detect = useCallback(async (): Promise<DetectedAccount[] | null> => {
    setIsLoading(true);
    setError(null);
    setElapsedSeconds(0);
    if (intervalRef.current !== null) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(
      () => setElapsedSeconds((s) => s + 1),
      1000,
    );
    try {
      const result = await step2DetectApiSetupStep2DetectPost();
      return result.detected_accounts;
    } catch (e: unknown) {
      setError(mapAxiosErrorToDetectError(e));
      return null;
    } finally {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      setIsLoading(false);
    }
  }, []);

  const detectFromXml = useCallback(
    async (file: File): Promise<DetectedAccount[] | null> => {
      setIsLoading(true);
      setError(null);
      setElapsedSeconds(0);
      try {
        const result = await step2DetectFromXmlApiSetupStep2DetectFromXmlPost({
          file,
        });
        return result.detected_accounts;
      } catch (e: unknown) {
        setError(mapAxiosErrorToDetectError(e));
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  return { detect, detectFromXml, isLoading, elapsedSeconds, error };
}
