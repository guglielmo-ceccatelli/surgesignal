import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from surgesignal.engine.params import load_params
from surgesignal.engine.types import Disruption, ServiceArea, Station

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tfl"

# Morden branch, in route order (real TfL stationIds from the Route/Sequence fixture).
MORDEN_BRANCH = (
    "940GZZLUSKW",  # Stockwell
    "940GZZLUCPN",  # Clapham North
    "940GZZLUCPC",  # Clapham Common
    "940GZZLUCPS",  # Clapham South
    "940GZZLUBLM",  # Balham
    "940GZZLUTBC",  # Tooting Bec
    "940GZZLUTBY",  # Tooting Broadway
    "940GZZLUCSD",  # Colliers Wood
    "940GZZLUSWN",  # South Wimbledon
    "940GZZLUMDN",  # Morden
)
OVAL = "940GZZLUOVL"
KENNINGTON = "940GZZLUKNG"

# Friday 25 Sep 2026, 18:07 London time (BST = UTC+1).
T0 = datetime(2026, 9, 25, 17, 7, tzinfo=timezone.utc)
NOW = T0 + timedelta(minutes=10)  # past the 8-min ramp


@pytest.fixture(scope="session")
def params():
    return load_params()


@pytest.fixture(scope="session")
def northern_stations():
    data = json.loads((FIXTURES / "route_sequence_northern_outbound_2026-09-23.json").read_text())
    stations = {}
    for seq in data["stopPointSequences"]:
        for sp in seq["stopPoint"]:
            name = sp["name"].replace(" Underground Station", "")
            stations[sp["stationId"]] = Station(sp["stationId"], name, sp["lat"], sp["lon"])
    return stations


@pytest.fixture
def wide_area(northern_stations):
    s = northern_stations["940GZZLUCPC"]  # Clapham Common
    return ServiceArea(s.lat, s.lon, radius_km=10)


def make_disruption(**kw):
    base = dict(
        key="northern-suspended-stockwell-morden",
        line="northern",
        line_name="Northern",
        severity_class="suspended",
        is_unplanned=True,
        affected_station_ids=MORDEN_BRANCH,
        t0=T0,
    )
    base.update(kw)
    return Disruption(**base)
