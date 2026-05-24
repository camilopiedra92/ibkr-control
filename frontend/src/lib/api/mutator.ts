import Axios, { AxiosRequestConfig } from "axios";

const baseURL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

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

export const axiosMutator = <T>(config: AxiosRequestConfig): Promise<T> => {
  return axiosInstance.request<T, T>(config);
};

export default axiosMutator;
