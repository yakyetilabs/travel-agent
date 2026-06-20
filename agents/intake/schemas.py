from typing import Literal

from pydantic import BaseModel, Field, model_validator

TravelClass = Literal["economy", "premium_economy", "business", "first"]
TripPurpose = Literal["client_meeting", "conference", "internal_training", "other"]

# The fare engine rejects requests whose passenger counts sum to more than 9
# (DECISIONS.md §3). Mirror that cap here so intake fails fast instead of
# producing a trip the engine will reject downstream.
MAX_TOTAL_PASSENGERS = 9


class TravelerProfile(BaseModel):
    name: str | None = None
    email: str | None = None
    employee_id: str | None = None
    department: str | None = None


class PassengerGroup(BaseModel):
    count: int = Field(ge=1, le=9)
    type: Literal["adult", "child", "infant"]


class TripRequest(BaseModel):
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None  # ISO 8601
    return_date: str | None = None
    passengers: list[PassengerGroup] = Field(default_factory=list)
    travel_class: TravelClass | None = None
    trip_purpose: TripPurpose | None = None

    @model_validator(mode="after")
    def _check_total_passengers(self) -> "TripRequest":
        total = sum(g.count for g in self.passengers)
        if total > MAX_TOTAL_PASSENGERS:
            raise ValueError(
                f"total passenger count {total} exceeds maximum {MAX_TOTAL_PASSENGERS}"
            )
        return self


class IntakeOutput(BaseModel):
    traveler: TravelerProfile
    trip: TripRequest
    missing_fields: list[str] = Field(default_factory=list)
    ready_for_policy: bool
