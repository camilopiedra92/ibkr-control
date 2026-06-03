import { redirect } from "next/navigation";

export default function Home() {
  // The root path has no content of its own: authenticated users belong on the
  // dashboard, and `AuthGate` (in the (app) layout) bounces unauthenticated
  // users to /login.
  redirect("/dashboard");
}
