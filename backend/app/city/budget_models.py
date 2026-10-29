"""Explicit currency units and visitor budget intentions; amounts are integer fen."""
from datetime import date
from pydantic import AwareDatetime, model_validator
from typing import Literal
from pydantic import Field
from .schema import Strict


class SpendingReport(Strict):
    amount_fen: int = Field(ge=0,le=100000000)
    as_of: AwareDatetime
    covered_cost_ids: tuple[str,...] = Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.covered_cost_ids))!=len(self.covered_cost_ids):raise ValueError('已支付费用项不能重复')
        return self


class PricingOptions(Strict):
    party_size: int | None = Field(default=None,ge=1,le=50)
    vehicle_count: int = Field(default=1,ge=1,le=20)
    driving_basis: Literal['taxi','self_drive'] = 'taxi'
    total_budget_fen: int | None = Field(default=None,ge=0,le=100000000)
    per_person_budget_fen: int | None = Field(default=None,ge=0,le=100000000)
    per_person_daily: bool = False
    dining_per_person_day_fen: int | None = Field(default=None,ge=0,le=1000000)
    hard_limit: bool = False
    spending: SpendingReport | None = None

    @model_validator(mode='after')
    def hard_budget(self):
        if self.hard_limit and self.total_budget_fen is None and (self.per_person_budget_fen is None or self.party_size is None):
            raise ValueError('硬预算需要全程总额，或明确人数及人均预算')
        return self


class StayPriceInput(Strict):
    amount_fen: int = Field(ge=0,le=100000000)
    unit: Literal['per_room_night','per_room_stay','party_stay']
    rooms: int = Field(default=1,ge=1,le=20)


class CostItem(Strict):
    cost_id: str
    category: Literal['tickets','dining','transport','lodging','other','spent']
    label: str
    day: date | None = None
    amount_fen: int | None = Field(default=None,ge=0)
    unit: str
    quantity: int | None = Field(default=None,ge=0)
    basis: Literal['quoted','estimate','reported','unknown']
    source: str
    notes: tuple[str,...] = ()
    covered_cost_ids: tuple[str,...] = ()


class BudgetSummary(Strict):
    currency: Literal['CNY'] = 'CNY'
    scope: Literal['city_trip'] = 'city_trip'
    items: tuple[CostItem,...]
    known_total_fen: int = Field(ge=0)
    per_person_fen: int | None = Field(default=None,ge=0)
    limit_fen: int | None = Field(default=None,ge=0)
    remaining_fen: int | None = None
    status: Literal['not_set','within_budget','over','unknown']
    estimated: bool
    unknown_items: tuple[str,...]
    by_category: dict[str,int]
    by_day: dict[str,int]
    hard_limit: bool = False
