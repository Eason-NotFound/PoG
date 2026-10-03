import { redirect } from "next/navigation";
import { currentUser } from "@/lib/auth";
export const dynamic = "force-dynamic";
export default async function PaymentPage() {
  const u = await currentUser();
  if (!u) redirect("/login");
  if (u.role === "maintainer") redirect("/");
  redirect(`/${u.role}/funds`);
}
