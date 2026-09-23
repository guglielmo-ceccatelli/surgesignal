from datetime import datetime, timedelta, timezone

from surgesignal.alerts.lifecycle import Context
from surgesignal.engine import model
from surgesignal.engine.geo import in_service_area
from surgesignal.engine.types import Conditions, Disruption, ServiceArea
from tests.conftest import BUILDER, DISPATCHER, DRIVER, STRANGER  # noqa: F401  (re-exported)

T0 = datetime(2026, 9, 25, 17, 7, tzinfo=timezone.utc)  # 18:07 London
NOW = T0 + timedelta(minutes=10)
MORDEN_BRANCH = ("940GZZLUSKW", "940GZZLUCPN", "940GZZLUCPC", "940GZZLUCPS", "940GZZLUBLM",
                 "940GZZLUTBC", "940GZZLUTBY", "940GZZLUCSD", "940GZZLUSWN", "940GZZLUMDN")


def northern(**kw):
    base = dict(key="northern:morden", line="northern", line_name="Northern", severity_class="suspended",
                is_unplanned=True, affected_station_ids=MORDEN_BRANCH, t0=T0)
    base.update(kw)
    return Disruption(**base)


def make_ctx(net, params, disruptions, now=NOW, idle=6, centre="940GZZLUCPC", radius=5.0,
             suppressed=frozenset(), quiet=None, conditions=Conditions()):
    c = net.stations[centre]
    area = ServiceArea(c.lat, c.lon, radius)
    result = model.run(net.stations, disruptions, conditions, {s: 1.0 for s in net.stations}, area, now, params)
    return Context(
        result=result,
        line_stations={lid: line.station_ids for lid, line in net.lines.items()},
        stations_in_area=frozenset(s.id for s in net.stations.values() if in_service_area(s, area)),
        idle_drivers=idle,
        suppressed_station_ids=frozenset(suppressed),
        quiet=quiet,
    )


