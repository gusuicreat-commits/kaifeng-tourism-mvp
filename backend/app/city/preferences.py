"""Visitor preferences shared by single-day and whole-trip planning conditions."""
from typing import Literal
from pydantic import Field, model_validator
from .schema import Strict

Category=Literal['shows','food','night','scenic']
CATEGORY_NAMES={'shows':'演出','food':'美食','night':'夜游','scenic':'景区'}


class TripPreferences(Strict):
    spice: Literal['none','mild','any'] | None = None
    interests: tuple[Category,...] = Field(default=(),max_length=4)
    avoid: tuple[Category,...] = Field(default=(),max_length=4)

    @model_validator(mode='after')
    def exclusive(self):
        overlap=set(self.interests)&set(self.avoid)
        if overlap:raise ValueError('同一类别不能同时选为更感兴趣和不想安排：'+'、'.join(CATEGORY_NAMES[k] for k in CATEGORY_NAMES if k in overlap))
        return self
