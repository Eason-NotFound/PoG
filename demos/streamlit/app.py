"""PoG presentation-only UI. No API, database, model, wallet or chain connections."""

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
from html import escape

import streamlit as st


SCENES = {
    "review": "1 · Review required — 8 of 10 received",
    "ready": "2 · Ready for release — all conditions recorded",
    "released": "3 · Released to Foundation — supplier not paid",
    "settled": "4 · Supplier settlement recorded — simulated",
}
VIEWS = {"donor": "Donor view", "approval": "Approval desk", "how": "How it works"}
FUNDING = 5_000
INVOICE = 2_500
ORDERED = 10
UNIT_PRICE = 250


@dataclass(frozen=True)
class Snapshot:
    scene: str
    received: int
    receipt_confirmed: bool
    human_approved: bool
    released: int
    supplier_paid: int

    @property
    def locked(self):
        return FUNDING - self.released

    @property
    def reserved(self):
        return INVOICE if not self.released else 0

    @property
    def can_release(self):
        return self.receipt_confirmed and self.human_approved and self.released == 0


def snapshot(scene):
    """Independent fictional snapshots, not live transitions or partial-payment logic."""
    if scene not in SCENES:
        raise ValueError("Unknown demonstration scene")
    complete = scene != "review"
    return Snapshot(
        scene, ORDERED if complete else 8, complete, complete,
        INVOICE if scene in {"released", "settled"} else 0,
        INVOICE if scene == "settled" else 0,
    )


def money(amount):
    return f"HK${amount:,.0f}"


def evidence_items(state):
    return [
        ("Purchase order", "DEMO-PO-001", f"DEMO ONLY\n10 desk fans for older residents\nUnit price: HKD 250\nOrder total: HKD 2500\nSupplier: Example supplier\n"),
        ("Invoice", "DEMO-INV-001", "DEMO ONLY\nInvoice for 10 desk fans\nTotal: HKD 2500\nPayee: Example supplier\nNo real invoice or payment\n"),
        ("Delivery record", f"DEMO-DEL-{state.received:02d}", f"DEMO ONLY\nOrdered: 10 desk fans\nReported received: {state.received}\nSubmitted by: Example receiving team\nFinal receipt confirmation: {'recorded in this fictional snapshot' if state.receipt_confirmed else 'not recorded; human review required'}\n"),
    ]


CSS = """
<style>
:root { --pog-green:#266a4e; --pog-ink:#203c31; --pog-muted:#6d7e75; }
.stApp { background:#f5f7f6; color:var(--pog-ink); }
.block-container { max-width:1280px; padding:2rem 2.4rem 3rem; }
[data-testid="stHeader"] { display:none; }
[data-testid="stDecoration"] { display:none; }
.pog { font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.pog * { box-sizing:border-box; }
.pog h1,.pog h2,.pog h3,.pog p { margin:0; color:var(--pog-ink); }
.pog h1 { font-size:clamp(32px,4vw,52px); line-height:1.12; letter-spacing:-1.8px; font-weight:750; }
.pog h2 { font-size:23px; line-height:1.3; letter-spacing:-.45px; font-weight:700; }
.pog h3 { font-size:17px; font-weight:650; }
.pog p { font-size:15px; line-height:1.65; }
.pog .muted { color:var(--pog-muted); }
.pog .small { font-size:12px; line-height:1.6; }
.pog .eyebrow { text-transform:uppercase; font-size:11px; letter-spacing:1.8px; font-weight:700; color:var(--pog-muted); margin-bottom:11px; }
.pog .top { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:26px; }
.pog .brand { display:flex; align-items:center; gap:11px; font-size:17px; font-weight:700; }
.pog .logo { display:grid; place-items:center; width:49px; height:49px; border-radius:14px; background:#266a4e; color:white; font-size:21px; letter-spacing:-1px; }
.pog .pill { display:inline-flex; align-items:center; gap:7px; border-radius:20px; padding:7px 12px; background:#e7efe9; color:#266a4e; font-size:12px; font-weight:650; line-height:1.4; }
.pog .pill.amber { background:#fbefda; color:#805318; }
.pog .pill.gray { background:#edf0ee; color:#5b6e63; }
.pog .hero { display:grid; grid-template-columns:1.25fr .75fr; gap:32px; align-items:center; margin-bottom:22px; }
.pog .hero p { margin-top:15px; max-width:620px; }
.pog .fan-panel { display:flex; align-items:center; gap:18px; background:#eaf3ed; border:1px solid #d8e6dc; border-radius:20px; padding:22px; }
.pog .fan-panel img { flex:0 0 82px; width:82px; height:104px; }
.pog .notice { background:#fff7e8; border:1px solid #eedbb3; border-radius:12px; padding:12px 16px; font-size:12px; line-height:1.65; margin:14px 0 17px; color:#77521f; }
.pog .stats { display:grid; grid-template-columns:repeat(4,1fr); gap:13px; margin:10px 0 8px; }
.pog .metric { padding:19px; background:white; border:1px solid #e1e8e3; border-radius:14px; }
.pog .metric .value { font-size:30px; font-weight:740; letter-spacing:-1px; margin:6px 0; }
.pog .metric .label { font-size:12px; color:#6d7e75; }
.pog .split { display:grid; grid-template-columns:1.2fr 1fr; gap:18px; margin:21px 0; }
.pog .card { background:white; border:1px solid #e1e8e3; border-radius:17px; padding:24px; }
.pog .card h2 { margin:8px 0 13px; }
.pog .callout { padding:16px; border-radius:12px; background:#edf5ef; border:1px solid #d9e8dd; margin-top:16px; }
.pog .callout.amber { background:#fff7e8; border-color:#eedbb3; }
.pog .row { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:11px 0; border-bottom:1px solid #edf1ee; font-size:14px; }
.pog .row:last-child { border-bottom:0; }
.pog .row strong { text-align:right; font-weight:650; }
.pog .fan-count { display:flex; gap:7px; flex-wrap:wrap; margin:17px 0 12px; }
.pog .fan-unit { display:grid; place-items:center; background:#266a4e; color:white; width:33px; height:33px; border-radius:9px; font-size:12px; font-weight:700; }
.pog .fan-unit.missing { background:#fff7e8; color:#94672c; border:1px dashed #c9a668; }
.pog .timeline { display:grid; gap:0; margin-top:7px; }
.pog .step { display:grid; grid-template-columns:27px 1fr auto; gap:9px; align-items:center; padding:10px 0; border-bottom:1px solid #edf1ee; font-size:13px; }
.pog .step:last-child { border:0; }
.pog .dot { width:23px; height:23px; display:grid; place-items:center; background:#e7efe9; border-radius:50%; color:#266a4e; font-size:11px; }
.pog .step-status { font-size:11px; color:#6d7e75; text-align:right; }
.pog .section { margin:25px 0 14px; }
.pog .process { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:19px 0; }
.pog .process .card { padding:20px; }
.pog .process .num { color:#266a4e; font-size:12px; font-weight:750; margin-bottom:10px; }
.pog .process p { margin-top:9px; font-size:13px; }
.pog .record { background:#edf5ef; padding:15px; border-radius:12px; margin-top:14px; }
.pog .footer { margin-top:28px; padding-top:18px; border-top:1px solid #dce5de; color:#6d7e75; font-size:12px; }
[data-testid="stExpander"] { background:white; border-radius:12px; }
@media(max-width:760px) {
 .block-container { padding:1.3rem 1rem 2rem; }
 .pog .hero,.pog .split { grid-template-columns:1fr; gap:14px; }
 .pog .hero { margin-bottom:15px; }
 .pog .hero h1 { letter-spacing:-1px; }
 .pog .stats { grid-template-columns:repeat(2,1fr); }
 .pog .process { grid-template-columns:1fr; }
 .pog .card { padding:19px; }
 .pog .top { align-items:flex-start; }
 .pog .brand small { display:none; }
 .pog .fan-panel { padding:16px; }
}
</style>
"""

FAN_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="82" height="104" viewBox="0 0 82 104" role="img" aria-label="Illustration of a desk fan">
<circle cx="41" cy="36" r="32" fill="white" stroke="#266a4e" stroke-width="2"/>
<circle cx="41" cy="36" r="25" fill="none" stroke="#c5d9cc"/>
<path d="M41 36 C21 38 20 19 34 16 C46 13 50 27 41 36 M41 36 C49 17 65 25 61 39 C57 51 44 48 41 36 M41 36 C54 51 39 65 29 53 C21 44 29 35 41 36" fill="#8bb59c"/>
<circle cx="41" cy="36" r="5" fill="#266a4e"/>
<path d="M37 69 L36 91 H46 L45 69 M23 97 Q41 84 59 97 Z" fill="#266a4e"/>
</svg>"""


def html(body):
    st.html(f'<div class="pog">{body}</div>')


def badge(text, tone=""):
    return f'<span class="pill {tone}">{escape(text)}</span>'


def row(label, value):
    return f'<div class="row"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>'


def metrics(state):
    values = [
        ("Project funding", FUNDING, "Fictional project total"),
        ("Still locked in project", state.locked, "Includes reserved funds"),
        ("Released to Foundation", state.released, "Not proof of supplier payment"),
        ("Supplier payment", state.supplier_paid, "Separate simulated settlement"),
    ]
    cards = ''.join(f'<div class="metric"><div class="label">{label}</div><div class="value">{money(value)}</div><div class="small muted">{note}</div></div>' for label, value, note in values)
    html(f'<div class="eyebrow">Demo balances · HKD-equivalent test units</div><div class="stats">{cards}</div><p class="small muted">Supplier payment is part of the released amount, not an additional outflow. No individual-donation allocation proof is shown.</p>')


def report_text(state):
    if state.scene == "review":
        return "The order and invoice list 10 fans, but the submitted delivery record reports 8. Two fans need clarification. Ask a person to verify delivery and resolve the mismatch before proceeding."
    return "The fictional records now list 10 fans. A separate final receipt confirmation and human release authorization are recorded in this example. A document match alone does not prove physical delivery."


def donor_view(state):
    statuses = [
        ("Project funding", "Recorded"),
        ("Purchase & budget reservation", "Recorded"),
        ("Final receipt confirmation", "Recorded" if state.receipt_confirmed else "Review required"),
        ("Human release approval", "Recorded" if state.human_approved else "Waiting"),
        ("Release to Foundation", "Recorded" if state.released else "Not released"),
        ("Supplier settlement", "Recorded · simulated" if state.supplier_paid else "Not recorded"),
    ]
    steps = ''.join(f'<div class="step"><span class="dot">{i}</span><span>{title}</span><span class="step-status">{status}</span></div>' for i, (title, status) in enumerate(statuses, 1))
    status, tone = ("Review required", "amber") if state.scene == "review" else ("Ready for release", "") if not state.released else ("Supplier settlement recorded · simulated", "") if state.supplier_paid else ("Released to Foundation", "")
    message = "Only 8 of 10 fans are reported received. No money is released. A person must check the discrepancy." if state.scene == "review" else "Evidence, final receipt confirmation and human authorization are present. The full invoice amount is ready for release; nothing has moved yet." if not state.released else "The invoice amount has been released to the Foundation in this fictional snapshot. Supplier payment is still outstanding." if not state.supplier_paid else "The fictional supplier payment and separate human settlement confirmation are recorded. Unused project funds remain locked."
    facts = row("Ordered", "10 fans") + row("Reported received", f"{state.received} fans") + row("Unit price · demo", money(UNIT_PRICE)) + row("Invoice total · demo", money(INVOICE))
    html(f'<div class="split"><section class="card"><div class="eyebrow">Project DEMO-FANS-001 · Fictional example</div><h2>Fans for older residents</h2><p class="muted">An example foundation is buying 10 desk fans. Follow the purchase, delivery and funding decisions.</p><div class="callout {tone}">{badge(status, tone)}<p style="margin-top:10px">{message}</p></div><div class="timeline">{steps}</div></section><section class="card"><div class="eyebrow">The purchase behind the donation</div><h2>One project. A visible paper trail.</h2>{facts}<div class="record"><p class="small">Reserved for this purchase: <strong>{money(state.reserved)} · demo</strong><br>Other project funds stay locked. This page does not trace each donated dollar to an individual fan.</p></div><p class="small muted" style="margin-top:14px">All records and balances on this page are fixed examples, not live project activity.</p></section></div>')


def approval_view(state):
    units = ''.join(f'<span class="fan-unit {"missing" if i > state.received else ""}">{i}</span>' for i in range(1, 11))
    result = "Review required" if state.scene == "review" else "Materials consistent · example only"
    conditions = row("Earlier procurement review & reservation", "Assumed complete · demo") + row("Final receipt confirmation", "Recorded · demo" if state.receipt_confirmed else "Missing") + row("Human release authorization", "Recorded · demo" if state.human_approved else "Pending") + row("Approved invoice amount", money(INVOICE) + " · demo")
    decision = "Blocked — resolve the mismatch first" if state.scene == "review" else "Ready — release only the invoice amount" if state.can_release else "Release already illustrated — do not repeat"
    html(f'<div class="split"><section class="card"><div class="eyebrow">Scripted AI finding · No model inference</div><h2>{result}</h2><p>{report_text(state)}</p><div class="fan-count">{units}</div><p class="small muted">Green = reported received. Dashed = not yet supported by this delivery record. The software cannot see the physical goods.</p><div class="callout amber"><h3>A person checks the evidence</h3><p class="small" style="margin-top:7px">Do not release 8/10 of the invoice automatically. Resolve the discrepancy, obtain the required receipt confirmation and review the full release request.</p></div></section><section class="card"><div class="eyebrow">Illustrative release gate · No signatures generated</div><h2>Human approval remains essential</h2>{conditions}<div class="record"><strong>{decision}</strong></div><p class="small muted" style="margin-top:13px">The complete snapshot includes an independent human authorization. Changing the snapshot does not create an approval, receipt signature or transfer.</p></section></div>')


def how_view():
    steps = [
        ("01", "Keep the original evidence", "Purchase orders, invoices and delivery records stay in controlled off-chain storage."),
        ("02", "Check the materials", "AI assists with document checks. A receiving team checks actual delivery; AI cannot observe the goods."),
        ("03", "Get human authorization", "A separate authorized person reviews the evidence and approves the exact invoice amount."),
        ("04", "Check release conditions", "The smart contract checks recorded evidence commitments, receipt confirmation and valid approvals before release."),
        ("05", "Release to the Foundation", "Only the approved invoice amount is released. Other funds stay locked while the project remains active."),
        ("06", "Record supplier settlement", "The Foundation completes payment. Payment evidence and human reconciliation are recorded separately."),
    ]
    cards = ''.join(f'<section class="card"><div class="num">{n}</div><h3>{title}</h3><p class="muted">{text}</p></section>' for n, title, text in steps)
    html(f'<div class="section"><div class="eyebrow">Intended architecture · Not connected in this demo</div><h2>Files off-chain. Checkable records on-chain.</h2><p class="muted" style="margin-top:10px">A blockchain keeps shared records that can be checked. A smart contract applies release rules to those records. Neither one proves that goods physically arrived.</p></div><div class="process">{cards}</div><div class="split"><section class="card"><h3>Off-chain</h3><p style="margin-top:9px">Original files, sensitive information, AI reports and payment evidence are held in controlled storage.</p></section><section class="card"><h3>On-chain in the intended system</h3><p style="margin-top:9px">Document fingerprints, signed confirmations, approval commitments and relevant funding events are recorded. Full private invoices are not published.</p></section></div>')


def evidence_panel(state):
    html('<div class="section"><div class="eyebrow">Fictional evidence pack</div><h2>Open the records behind the status</h2><p class="muted" style="margin-top:8px">These are text-only demo documents. There are no private files or real supplier records.</p></div>')
    for title, record_id, body in evidence_items(state):
        with st.expander(f"{title} · {record_id}"):
            st.text(body)
            st.caption("Demo SHA-256 fingerprint of the fictional text above. Not a blockchain transaction or proof of delivery.")
            st.code(sha256(body.encode("utf-8")).hexdigest(), language=None)
            st.download_button("Download demo text", body, file_name=f"{record_id}.txt", mime="text/plain", key=f"download_{record_id}")


def main():
    st.set_page_config(page_title="PoG | See How Your Donation Is Used", layout="wide")
    st.html(CSS)
    fan_image = '<img alt="Illustration of a desk fan" src="data:image/svg+xml;base64,' + b64encode(FAN_SVG.encode()).decode() + '">'
    html(f'<header class="top"><div class="brand"><span class="logo">PoG</span><div>Proof of Giving<br><small class="muted" style="font-weight:400;font-size:12px">Donation transparency prototype</small></div></div>{badge("DEMO · No live integrations", "amber")}</header><section class="hero"><div><div class="eyebrow">Make the journey visible</div><h1>See how your<br>donation is used.</h1><p class="muted">Follow a project from a purchase request to delivery, human approval and recorded settlement.</p></div><div class="fan-panel">{fan_image}<div><div class="eyebrow">A simple example</div><h3>10 fans for older residents</h3><p class="small muted" style="margin-top:7px">One purchase. Evidence to inspect.<br>A person accountable for approval.</p></div></div></section><div class="notice"><strong>DEMO DATA ONLY.</strong> All amounts, documents, fingerprints and statuses are fictional. No AI inference, real funds, bank, wallet, database or blockchain connection. Switching a snapshot does not execute a transaction.</div>')
    query_scene = st.query_params.get("scene", "review")
    query_view = st.query_params.get("view", "donor")
    columns = st.columns([1.1, 1.4], gap="large")
    with columns[0]:
        view = st.radio("Explore the demo", list(VIEWS), index=list(VIEWS).index(query_view) if query_view in VIEWS else 0, format_func=VIEWS.get, horizontal=True, key="view")
    with columns[1]:
        scene = st.selectbox("Load a fictional snapshot", list(SCENES), index=list(SCENES).index(query_scene) if query_scene in SCENES else 0, format_func=SCENES.get, key="scene")
    st.query_params.update({"scene": scene, "view": view})
    state = snapshot(scene)
    metrics(state)
    if view == "donor":
        donor_view(state)
    elif view == "approval":
        approval_view(state)
    else:
        how_view()
    if view != "how":
        evidence_panel(state)
    html('<footer class="footer">PoG · Presentation-only prototype. V2 distinction: Foundation release is not supplier settlement.<br>No customer, partnership, fraud-detection accuracy, revenue or production-readiness claim is made.</footer>')


if __name__ == "__main__":
    main()
