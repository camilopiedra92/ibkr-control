import Axios, { AxiosRequestConfig } from "axios";

import { clearToken, getToken } from "@/lib/auth/storeToken";

const baseURL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const axiosInstance = Axios.create({ baseURL });

axiosInstance.interceptors.request.use((cfg) => {
  const token = getToken();
  if (token) {
    cfg.headers = cfg.headers ?? {};
    cfg.headers["Authorization"] = `Bearer ${token}`;
  }
  return cfg;
});

axiosInstance.interceptors.response.use(
  (r) => r,
  (error: unknown) => {
    if (
      typeof window !== "undefined" &&
      Axios.isAxiosError(error) &&
      error.response?.status === 401 &&
      !window.location.pathname.startsWith("/login")
    ) {
      clearToken();
      window.location.replace("/login");
    }
    // Axios always rejects with an AxiosError (an Error subclass); the wrapper
    // is a type-safety fallback for the theoretical non-Error case.
    return Promise.reject(
      error instanceof Error ? error : new Error("Request failed"),
    );
  },
);

export const axiosMutator = <T>(config: AxiosRequestConfig): Promise<T> => {
  // axios.request returns AxiosResponse<T> with .data, .headers, .status, etc.
  // Orval-generated code expects T directly — unwrap .data here so the type
  // contract (Promise<T>) matches runtime behavior.
  return axiosInstance.request<T>(config).then((r) => r.data);
};

export default axiosMutator;
