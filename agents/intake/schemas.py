from typing import Literal

from pydantic import BaseModel, Field, model_validator

TravelClass = Literal["economy", "premium_economy", "business", "first"]
TripPurpose = Literal["client_meeting", "conference", "internal_training", "other"]
# Human-shaped counterpart of the engine contract's journey_type: it resolves the
# ambiguity of a missing return_date (one-way trip vs. not-yet-answered).
TripType = Literal["one_way", "round_trip"]

# The fare engine caps SEATED passengers (adults + children) at 9 and allows at
# most one lap infant per adult (engine DECISIONS.md §4). Mirror both rules here
# so intake fails fast instead of producing a trip the engine will reject
# downstream.
MAX_SEATED_PASSENGERS = 9


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
    trip_type: TripType | None = None
    departure_date: str | None = None  # ISO 8601
    return_date: str | None = None  # required iff trip_type == "round_trip"
    passengers: list[PassengerGroup] = Field(default_factory=list)
    travel_class: TravelClass | None = None
    trip_purpose: TripPurpose | None = None

    @model_validator(mode="after")
    def _check_passenger_rules(self) -> "TripRequest":
        seated = sum(g.count for g in self.passengers if g.type in ("adult", "child"))
        adults = sum(g.count for g in self.passengers if g.type == "adult")
        infants = sum(g.count for g in self.passengers if g.type == "infant")
        if seated > MAX_SEATED_PASSENGERS:
            raise ValueError(
                f"seated passenger count {seated} (adults + children) "
                f"exceeds maximum {MAX_SEATED_PASSENGERS}"
            )
        if infants > adults:
            raise ValueError(
                f"infant count {infants} exceeds adult count {adults}: "
                "one lap infant per adult"
            )
        return self


class IntakeOutput(BaseModel):
    traveler: TravelerProfile
    trip: TripRequest
    missing_fields: list[str] = Field(default_factory=list)
    ready_for_policy: bool
