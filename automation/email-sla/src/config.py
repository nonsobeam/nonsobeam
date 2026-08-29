"""Run configuration: who the owner is, and which accounts their contacts map to.

Nothing secret lives here. The one credential this project can use (an optional
LLM API key) is read from the environment by `llm.py`, never from a config file
and never from source.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from schema import normalise_address

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")


@dataclass
class Config:
    #: Every address that is the mailbox owner -- aliases included, so mail to an
    #: old address still counts as addressed to them.
    me: set[str] = field(default_factory=set)
    #: Address or domain -> account name. Only exact matches are used; an
    #: unmatched thread renders as "not identified" rather than a guess.
    accounts: dict[str, str] = field(default_factory=dict)
    owner_name: str = "the mailbox owner"
    months: int = 11

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = path or DEFAULT_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No config at {path}. Copy config.example.json to config.json "
                "and set your own address(es)."
            )
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            me={normalise_address(a) for a in raw.get("me", []) if a},
            accounts={
                normalise_address(k) if "@" in k else k.casefold(): v
                for k, v in (raw.get("accounts") or {}).items()
            },
            owner_name=raw.get("owner_name", "the mailbox owner"),
            months=int(raw.get("months", 11)),
        )

    def validate(self) -> None:
        if not self.me:
            raise ValueError(
                "config.json must list at least one address under \"me\" -- "
                "without it nothing can be told inbound from outbound."
            )
