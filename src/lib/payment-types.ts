export type FundsState = "Active" | "Closing" | "Refundable" | "Closed" | "Pending";
export type PaymentWallet = { hkdCents: number; tokens: string };
export type ExchangeOrder = {
  id: string;
  userId: string;
  projectId: string;
  cents: number;
  status: "frozen" | "completed" | "cancelled";
  createdAt: string;
};
export type ProjectFunds = {
  state: FundsState;
  paused: boolean;
  deposited: string;
  reserved: string;
  released: string;
  returned: string;
  refunded: string;
  donors: Record<string, string>;
  entitlements: Record<string, string>;
  claimed: Record<string, boolean>;
};
export type PaymentRelease = {
  id: string;
  projectId: string;
  procurementId: string;
  foundationId: string;
  amount: string | null;
  redeemed: string | null;
  returned: string | null;
  externalReference?: string;
};
export type PaymentEvent = {
  id: string;
  projectId: string;
  kind: string;
  status?: string;
  actorId: string;
  actorRole: string;
  amount: string;
  unit?: "HKD" | "mHKD";
  from: string;
  to: string;
  at: string;
  reference?: string;
  receiptHash: string;
};
export type PaymentBook = {
  version: 1;
  wallets: Record<string, PaymentWallet>;
  exchanges: ExchangeOrder[];
  projects: Record<string, ProjectFunds>;
  releases: PaymentRelease[];
  events: PaymentEvent[];
  minted: string;
  redemptionTokens: string;
  intents: Record<
    string,
    { fingerprint: string; result: Record<string, unknown> }
  >;
};
export type FundsView = {
  mode: "integrated-demo" | "a2-local-simulation";
  updatedAt: string;
  user: {
    id: string;
    role: string;
    hkdCents: string | null;
    availableHkdCents: string | null;
    holdCents: string | null;
    tokens: string | null;
  };
  globalDonors: { tokens: string | null; frozenHkdCents: string | null };
  exchanges: ExchangeOrder[];
  projects: {
    id: string;
    name: string;
    state: FundsState;
    paused: boolean;
    deposited: string | null;
    locked: string | null;
    reserved: string | null;
    free: string | null;
    foundationTokens: string | null;
    released: string | null;
    returned: string | null;
    redeemed: string | null;
    refunded: string | null;
    returnable: string | null;
    donorCount: number | null;
    myDonation: string | null;
    myRefund: string | null;
    myClaimed: boolean;
    balanced: boolean | null;
    unresolved: number;
    canManage: boolean;
    events: Omit<PaymentEvent, "actorId">[];
    releases: PaymentRelease[];
  }[];
};
