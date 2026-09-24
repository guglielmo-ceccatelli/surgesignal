"""The money logic behind SurgeSignal, as a small labelled calculator (The economics tab).

    rider's saving vs Uber, per trip   = fare × (surge − 1)          why riders switch to you
    pickup-time gain                    = pickup_cold − pickup_placed  why you can take the job
    extra revenue per week              = alerts/week × jobs won per alert × fare
    per year                            = × 52

Every input carries a kind: EVIDENCE (with a source) or ESTIMATE (an assumption, to be replaced
by operator data). The Grossman–Stiglitz framing: information is costly, so whoever acts on it
first earns the return; SurgeSignal turns a free public signal (TfL status) into an action minutes
before demand shows up in a firm's own bookings.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Input:
    label: str
    value: float
    unit: str
    kind: str  # "EVIDENCE" | "ESTIMATE"
    source: str


DEFAULTS: dict[str, Input] = {
    "fare_gbp": Input("Your fixed fare for a typical 3-mile trip", 15.0, "£", "ESTIMATE",
                      "Placeholder until the operator's real fare is known."),
    "surge_low": Input("Uber surge during disruptions, low", 1.5, "×", "EVIDENCE",
                       "Reported surges of about 1.9× and +150% (2.5×) on a Tube strike day (Time Out; Uber UK blog)."),
    "surge_high": Input("Uber surge during disruptions, high", 2.5, "×", "EVIDENCE",
                        "As above: +150% reported on the first day of a Tube strike."),
    "pickup_placed_min": Input("Pickup time with cars placed early", 5.0, "min", "ESTIMATE",
                               "Assumption: a car already waiting within about a mile."),
    "pickup_cold_min": Input("Pickup time without SurgeSignal", 20.0, "min", "ESTIMATE",
                             "Assumption: dispatching after the calls arrive, across a busy area."),
    "alerts_per_week": Input("Qualifying alerts per week in your area", 2.0, "", "ESTIMATE",
                             "Placeholder until the week-one count on Sep 30 (task T8)."),
    "jobs_per_alert": Input("Extra jobs won per alert", 3.0, "", "ESTIMATE",
                            "Assumption: a few riders priced out by the surge choose a nearby fixed-fare car."),
}


@dataclass(frozen=True)
class EconInputs:
    fare_gbp: float = DEFAULTS["fare_gbp"].value
    surge_low: float = DEFAULTS["surge_low"].value
    surge_high: float = DEFAULTS["surge_high"].value
    pickup_placed_min: float = DEFAULTS["pickup_placed_min"].value
    pickup_cold_min: float = DEFAULTS["pickup_cold_min"].value
    alerts_per_week: float = DEFAULTS["alerts_per_week"].value
    jobs_per_alert: float = DEFAULTS["jobs_per_alert"].value

    def __post_init__(self) -> None:
        for f in fields(self):
            if getattr(self, f.name) < 0:
                raise ValueError(f"{DEFAULTS[f.name].label} can't be negative")
        if self.surge_low < 1 or self.surge_high < self.surge_low:
            raise ValueError("Surge must be at least 1×, and the high end at least the low end")


@dataclass(frozen=True)
class EconResult:
    rider_saving_low: float  # £ per trip a rider saves by choosing you over surging Uber
    rider_saving_high: float
    pickup_gain_min: float
    weekly_revenue: float
    yearly_revenue: float
    jobs_per_week: float


def evaluate(i: EconInputs) -> EconResult:
    jobs = i.alerts_per_week * i.jobs_per_alert
    weekly = jobs * i.fare_gbp
    return EconResult(
        rider_saving_low=i.fare_gbp * (i.surge_low - 1),
        rider_saving_high=i.fare_gbp * (i.surge_high - 1),
        pickup_gain_min=max(0.0, i.pickup_cold_min - i.pickup_placed_min),
        weekly_revenue=weekly,
        yearly_revenue=weekly * 52,
        jobs_per_week=jobs,
    )
