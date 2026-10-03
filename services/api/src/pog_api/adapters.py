from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


class DependencyUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AdapterStatus:
    name: str
    mode: str
    available: bool
    verified: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ChainAdapter(Protocol):
    def status(self) -> AdapterStatus: ...
    def submit(self, operation: dict[str, object]) -> None: ...


class AIAdapter(Protocol):
    def status(self) -> AdapterStatus: ...
    def assess(self, request: dict[str, object]) -> None: ...


class PaymentAdapter(Protocol):
    def status(self) -> AdapterStatus: ...
    def submit(self, request: dict[str, object]) -> None: ...


class UnavailableAdapter:
    def __init__(self, name: str, detail: str):
        self._status = AdapterStatus(name, "unavailable", False, False, detail)

    def status(self) -> AdapterStatus:
        return self._status

    def submit(self, operation: dict[str, object]) -> None:
        raise DependencyUnavailable(self._status.detail)

    def assess(self, request: dict[str, object]) -> None:
        raise DependencyUnavailable(self._status.detail)


class MockWalletAdapter:
    def status(self) -> AdapterStatus:
        return AdapterStatus(
            "wallet",
            "mock_limited",
            True,
            False,
            "A1 role-wallet authorization only; no signing or transaction submission",
        )

    def request_authorization(self, action: str) -> dict[str, str]:
        return {"status": "awaiting_authorization", "action": action, "simulated": "true"}


@dataclass(frozen=True)
class AdapterRegistry:
    chain: UnavailableAdapter
    ai: UnavailableAdapter
    payment: UnavailableAdapter
    wallet: MockWalletAdapter

    @classmethod
    def a1_default(cls) -> "AdapterRegistry":
        return cls(
            chain=UnavailableAdapter("chain", "A2 chain adapter is not implemented"),
            ai=UnavailableAdapter("ai", "A3 AI service is not implemented"),
            payment=UnavailableAdapter("payment", "A3 payment service is not implemented"),
            wallet=MockWalletAdapter(),
        )

    def statuses(self) -> dict[str, dict[str, object]]:
        return {
            "chain": self.chain.status().as_dict(),
            "ai": self.ai.status().as_dict(),
            "payment": self.payment.status().as_dict(),
            "wallet": self.wallet.status().as_dict(),
        }
