export type FundsState = "Active" | "Closing" | "Refundable" | "Closed";
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
  amount: string;
  redeemed: string;
  returned: string;
  externalReference?: string;
};
export type PaymentEvent = {
  id: string;
  projectId: string;
  kind: string;
  actorId: string;
  actorRole: string;
  amount: string;
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
  mode: "integrated-demo";
  updatedAt: string;
  user: {
    id: string;
    role: string;
    hkdCents: number;
    availableHkdCents: number;
    holdCents: number;
    tokens: string;
  };
  globalDonors: { tokens: string; frozenHkdCents: number };
  exchanges: ExchangeOrder[];
  projects: {
    id: string;
    name: string;
    state: FundsState;
    paused: boolean;
    deposited: string;
    locked: string;
    reserved: string;
    free: string;
    foundationTokens: string;
    redeemed: string;
    refunded: string;
    returnable: string;
    donorCount: number;
    myDonation: string;
    myRefund: string;
    myClaimed: boolean;
    balanced: boolean;
    unresolved: number;
    canManage: boolean;
    events: Omit<PaymentEvent, "actorId">[];
    releases: PaymentRelease[];
  }[];
};
