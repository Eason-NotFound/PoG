import { notFound, redirect } from "next/navigation";
import { currentUser } from "@/lib/auth";
import { canRead, firstPath, pageByPath } from "@/lib/access";
import { pageData } from "@/lib/service";
import PortalApp from "@/components/portal-app";
import AccessMessage from "@/components/access-message";
export const dynamic = "force-dynamic";
export default async function Workspace({
  params,
}: {
  params: Promise<{ portal: string; page: string }>;
}) {
  const route = await params,
    p = pageByPath(route.portal, route.page);
  if (!p) notFound();
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
