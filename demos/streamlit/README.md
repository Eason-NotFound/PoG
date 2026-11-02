# PoG Streamlit showcase

An independent English presentation UI for **PoG: See How Your Donation Is Used**.
It reuses the portal's name and green palette, not its API, account data or runtime.
All data is fictional. No secrets or other PoG services are required.

## Run from the repository root

Use Python 3.12 or newer (locally verified with Python 3.14).

```sh
python -m venv .venv
# macOS/Linux; on Windows use .venv\Scripts\activate
source .venv/bin/activate
python -m pip install -r demos/streamlit/requirements.txt
python -m streamlit run demos/streamlit/app.py
```

Open the Local URL printed by Streamlit (normally http://localhost:8501).
This starts only the new showcase; it does not start/reset Anvil or PostgreSQL.
Choose a view and a fictional snapshot. Query parameters also select a default:
`?scene=review&view=approval`. Valid scenes: `review`, `ready`, `released`, `settled`.
Valid views: `donor`, `approval`, `how`. Unknown query values fall back safely.

## Deploy to Streamlit Community Cloud

In [Community Cloud](https://share.streamlit.io/), connect the GitHub account and
create an app with:

| Field | Value |
| --- | --- |
| Repository | `Eason-NotFound/PoG` |
| Branch | `codex/pog-streamlit-showcase` (until separately approved and merged) |
| Main file path | `demos/streamlit/app.py` |
| Python | 3.14, or an available compatible 3.12+ version |
| Secrets | None |

The entrypoint-local `requirements.txt` declares the only direct dependency.
No root requirements, Next.js build or API environment variables are needed.
Deploy only this entrypoint, not the repository's API. Obtain the live app URL
from Streamlit after deployment; pushing GitHub code does **not** deploy it.
An account with access to the repository and Community Cloud is still needed.

Official instructions: [deployment fields](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
and [dependency lookup](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).

## What the four snapshots illustrate

Fixed demo funding is HK$5,000 equivalent. The fictional order and invoice are
10 fans x HK$250 = HK$2,500. Earlier procurement review and budget reservation
are assumed complete. No receiving team, approver or AI has acted in this app.

| Snapshot | Reported received | Final receipt / human release approval | Released to Foundation | Supplier payment | Still locked |
| --- | --- | --- | --- | --- | --- |
| Review | 8 / 10 | Missing / pending | 0 | 0 | 5,000 |
| Ready | 10 / 10 | Recorded / recorded, fictional | 0 | 0 | 5,000 |
| Released | 10 / 10 | Recorded / recorded, fictional | 2,500 | 0 | 2,500 |
| Settled | 10 / 10 | Recorded / recorded, fictional | 2,500 | 2,500, simulated | 2,500 |

Switching snapshots **loads an independent example**; it does not approve,
sign or transfer anything. Review does not release 8/10 of the invoice.
The complete example includes both independent receipt confirmation and human
authorization; quantity matching alone never authorizes payment. The approved
invoice amount is released once to Foundation, never all project funds.
Supplier payment is a use of released funds, not a second outflow from escrow.
The active project's unused funds stay locked. These semantics follow
[`M2_V2_SPEC.md`](../../docs/M2_V2_SPEC.md).

The AI report is scripted text, not inference or a detection-accuracy result.
Downloads and SHA-256 fingerprints are generated from fictional text embedded
in this file, not actual evidence, transaction hashes or signed chain records.
The architecture view explains the intended off-chain/on-chain distinction;
the showcase connects to neither. There is no individual-donation allocation
proof, real payment, refund workflow, login or commercial pricing claim.

## Checks

```sh
python -m unittest discover -s demos/streamlit -p 'test_*.py' -v
```

Tests cover the four snapshots, money conservation, full-invoice gating,
fictional evidence fingerprints and Streamlit rendering/control changes.
Browser captures and local test logs are QA artifacts, not evidence of live
AI, bank or blockchain integration. Do not publish credentials, private project
evidence, screenshots of personal accounts, or local runtime data with this UI.
