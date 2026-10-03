import { currentUser } from "@/lib/auth";
import { allowedPages, firstPath } from "@/lib/access";
import { redirect } from "next/navigation";
import AccessMessage from "@/components/access-message";
export const dynamic = "force-dynamic";
export default async function NoAccess() {
  const u = await currentUser();
  if (!u) redirect("/login");
  if (allowedPages(u).length) redirect(firstPath(u));
  return <AccessMessage kind="unassigned" href="/login" />;
}
