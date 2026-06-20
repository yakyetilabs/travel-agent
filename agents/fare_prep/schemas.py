"""Schema for the fare-prep stage.

`fare_prep` derives the fare engine's boundary contract from intake. The actual
derivation lives in `tools/fare_request.py` (deterministic); this model documents
the shape the agent stages into session state for the remote engine to consume.
The enum aliases are re-exported from the tool module so the contract vocabulary
has a single definition in this repo.
"""

from pydantic import BaseModel, Field

from tools.fare_request import (
    BookingClass,
    CabinClass,
    PassengerType,
    RouteType,
    SeasonCode,
)


class PassengerGroup(BaseModel):
    count: int = Field(ge=1, le=9)
    type: PassengerType


class FareRequest(BaseModel):
    """Mirror of the engine's FareQuoteRequest (schema.go)."""

    base_distance_miles: int = Field(ge=100, le=10000)
    advance_purchase_days: int = Field(ge=0, le=365)
    passengers: list[PassengerGroup] = Field(min_length=1)
    cabin_class: CabinClass
    booking_class: BookingClass
    route_type: RouteType
    season_code: SeasonCode


class FarePrepOutput(BaseModel):
    """What fare_prep writes to state under `fare_request`.

    Either a built `fare_request`, or an `error` explaining why one could not be
    derived (e.g. unknown airport). Never both.
    """

    ok: bool
    fare_request: FareRequest | None = None
    error: str | None = None
