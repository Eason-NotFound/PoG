import Link from "next/link";
import { A2Workbench } from "@/components/a2-workbench";

export const dynamic = "force-dynamic";

export default function IntegrationPage() {
  if (process.env.POG_A2_INTEGRATION !== "true") {
    return (
      <main className="a2-workbench a2-unavailable">
        <p className="eyebrow">PoG · A2 INTEGRATION</p>
        <h1>整合工作台未啟用 / Integration disabled</h1>
        <p>此頁不使用 Portal 演示賬本。需要受控本地 A2 配置才能啟用。</p>
        <p>
          This workspace has no demo fallback. Enable the documented local A2
          integration configuration first.
        </p>
        <Link href="/login">返回舊版演示 / Legacy demo</Link>
      </main>
    );
  }
  return <A2Workbench />;
}
