import type { AccessUser } from "./access";
export type Project = {
  paymentTracked?: boolean;
  released?: number;
  refunded?: number;
  id: string;
  name: string;
  description: string;
  foundationId: string;
  recipientId: string;
  target: number;
  deposited: number;
  available: number;
  reserved: number;
  paid: number;
  rulesHash: string;
  categories: string[];
};
export type Donation = {
  id: string;
  ownerId: string;
  projectId: string;
  amount: number;
  allocated: number;
  hash: string;
  createdAt: string;
};
export type Procurement = {
  id: string;
  projectId: string;
  recipientId: string;
  vendorId: string;
  name: string;
  quantity: number;
  unitPrice: number;
  amount: number;
  status:
    | "human_review"
    | "reserved"
    | "payment_review"
    | "paid"
    | "funds_released"
    | "cancelled"
    | "needs_info"
    | "frozen";
  risk: number;
  finalRisk?: number;
  hash: string;
  createdAt: string;
  note: string;
  evidenceIds: string[];
  foundationProof?: ProofSubmission;
  recipientProof?: ProofSubmission;
};
export type Vendor = {
  id: string;
  name: string;
  verified: boolean;
  wallet: string;
};
export type ProofSubmission = {
  fileIds: string[];
  note: string;
  submittedAt: string;
  actorId: string;
  quantity?: number;
};
export type ReviewCase = {
  id: string;
  procurementId: string;
  projectId: string;
  stage: "purchase" | "delivery";
  status: "pending" | "reviewed";
  sourceHash: string;
  evidenceIds: string[];
  risk: number;
  createdAt: string;
  decision?: "approved" | "rejected" | "more_info";
  reason?: string;
  reviewerId?: string;
  reviewedAt?: string;
  legacy?: boolean;
};
export type Appeal = {
  id: string;
  reviewId: string;
  procurementId: string;
  projectId: string;
  ownerId: string;
  orgId: string;
  reason: string;
  status: "pending" | "resolved";
  createdAt: string;
  hash: string;
  resolution?: "accepted" | "rejected";
  response?: string;
  reviewerId?: string;
  resolvedAt?: string;
};
export type Evidence = {
  id: string;
  procurementId: string;
  ownerId: string;
  orgId: string;
  name: string;
  hash: string;
  mime: string;
  size: number;
  content: string;
  type: string;
  submittedRole?: "foundation" | "recipient";
  createdAt?: string;
};
export type RecordSnapshot = {
  id: string;
  kind: string;
  hash: string;
  title: string;
  ownerId?: string;
  orgId?: string;
  projectId?: string;
  amount?: number;
  unit?: string;
  createdAt: string;
  version: number;
  snapshot: Record<string, unknown>;
};
export type AuditLog = {
  id: string;
  actorId: string;
  actorName: string;
  action: string;
  target: string;
  at: string;
};
export type LedgerEntry = {
  id: string;
  projectId: string;
  kind: string;
  amount: number;
  reference: string;
  at: string;
};
export type PageData = {
  evidence?: Omit<Evidence, "content">[];
  reviews?: ReviewCase[];
  appeals?: Appeal[];
  projects?: Project[];
  donations?: Donation[];
  procurements?: Procurement[];
  vendors?: Vendor[];
  users?: AccessUser[];
  logs?: AuditLog[];
  ledger?: LedgerEntry[];
  reimbursements?: {
    id: string;
    name: string;
    amount: number;
    status: string;
    createdAt: string;
  }[];
  records?: RecordSnapshot[];
};
export type AppData = {
  user: AccessUser;
  pageId: string;
  data: PageData;
  updatedAt: string;
};
