import Axios, { AxiosRequestConfig } from "axios";

const baseURL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const axiosInstance = Axios.create({ baseURL });

axiosInstance.interceptors.request.use((cfg) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("auth_token");
    if (token) {
      cfg.headers = cfg.headers ?? {};
      cfg.headers["Authorization"] = `Bearer ${token}`;
    }
  }
  return cfg;
});

axiosInstance.interceptors.response.use(
  (r) => r,
  (error) => {
    if (
      typeof window !== "undefined" &&
      error?.response?.status === 401 &&
      !window.location.pathname.startsWith("/login")
    ) {
      localStorage.removeItem("auth_token");
      window.location.replace("/login");
    }
    return Promise.reject(error);
  },
);

export const axiosMutator = <T>(config: AxiosRequestConfig): Promise<T> => {
  // axios.request returns AxiosResponse<T> with .data, .headers, .status, etc.
  // Orval-generated code expects T directly — unwrap .data here so the type
  // contract (Promise<T>) matches runtime behavior.
  return axiosInstance.request<T>(config).then((r) => r.data);
};

export default axiosMutator;
