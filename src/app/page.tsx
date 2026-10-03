import { redirect } from "next/navigation";
import { currentUser } from "@/lib/auth";
import { firstPath } from "@/lib/access";
export default async function Home() {
  const u = await currentUser();
  redirect(u ? firstPath(u) : "/login");
}
