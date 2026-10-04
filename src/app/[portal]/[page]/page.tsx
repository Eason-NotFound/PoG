import { notFound, redirect } from "next/navigation";
import { currentUser } from "@/lib/auth";
import { canRead, firstPath, pageByPath } from "@/lib/access";
import { pageData } from "@/lib/service";
import PortalApp from "@/components/portal-app";
import AccessMessage from "@/components/access-message";
import { PortalA2Entry } from "@/components/portal-app";
export const dynamic = "force-dynamic";
export default async function Workspace({
  params,
}: {
  params: Promise<{ portal: string; page: string }>;
}) {
  const route = await params;
  const p = pageByPath(route.portal, route.page);
  if (!p) notFound();
  if (process.env.POG_A2_INTEGRATION === "true") {
    return <PortalA2Entry pageId={p.id} />;
  }
  const u = await currentUser();
  if (!u) redirect("/login");
  if (!canRead(u, p.id))
    return <AccessMessage kind="denied" name={u.name} href={firstPath(u)} />;
  return (
    <PortalApp
      initial={{
        user: u,
        pageId: p.id,
        data: pageData(u, p.id),
        updatedAt: new Date().toISOString(),
      }}
    />
  );
}
