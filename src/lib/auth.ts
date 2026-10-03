import { cookies } from "next/headers";
import { getUserByToken } from "./store";
export const SESSION_COOKIE = "pog_session";
export async function currentUser() {
  if (process.env.POG_DEMO_MODE !== "true") return null;
  return getUserByToken((await cookies()).get(SESSION_COOKIE)?.value);
}
