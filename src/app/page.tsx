import { redirect } from "next/navigation";
import { currentUser } from "@/lib/auth";
import { firstPath } from "@/lib/access";
export default async function Home() {
  if (process.env.POG_A2_INTEGRATION === "true") redirect("/login");
  const u = await currentUser();
  redirect(u ? firstPath(u) : "/login");
}
