"""Restaurant reference values shared by maintenance imports and read tools."""
import re
from decimal import Decimal


def price_range(value):
    match=re.fullmatch(r'\s*(\d+(?:\.\d{1,2})?)(?:\s*[-–~～]\s*(\d+(?:\.\d{1,2})?))?\s*(?:元(?:/人|／人)?)?\s*',value or '')
    if not match:return None
    low=int(Decimal(match[1])*100);high=int(Decimal(match[2] or match[1])*100)
    return (low,high) if 0<=low<=high<=100000000 else None
