import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from surgesignal.tfl.network import load_network
from surgesignal.tfl.parser import parse_status

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tfl"


@pytest.fixture(scope="module")
def net():
    return load_network()


def status(line, reason="", code=9, category="RealTime", affected=(), valid_from=None):
    ls = {"statusSeverity": code, "statusSeverityDescription": "x", "reason": reason,
          "validityPeriods": [], "disruption": {"category": category, "affectedStops": list(affected)}}
    if valid_from:
        ls["validityPeriods"] = [{"fromDate": valid_from, "toDate": "2026-09-24T00:29:00Z", "isNow": True}]
    if category is None:
        del ls["disruption"]
    return [{"id": line, "lineStatuses": [ls]}]


def names(net, inc):
    return sorted(net.stations[s].name for s in inc.station_ids)


def one(net, payload):
    r = parse_status(payload, net)
    assert len(r.incidents) == 1, (r.incidents, r.problems)
    return r.incidents[0], r.problems


# ---- real TfL data --------------------------------------------------------------------


def test_real_central_line_disruption_2026_09_23(net):
    payload = json.loads((FIXTURES / "status_2026-09-23_central-minor-delays.json").read_text())
    r = parse_status(payload, net)
    assert r.problems == []
    west, east = sorted(r.incidents, key=lambda i: "North Acton" not in names(net, i))
    assert names(net, west) == sorted([
        "North Acton", "West Acton", "Ealing Broadway",  # → Ealing Broadway branch
        "Hanger Lane", "Perivale", "Greenford", "Northolt", "South Ruislip", "Ruislip Gardens", "West Ruislip",
    ])
    east_names = names(net, east)
    assert {"Epping", "Theydon Bois", "Woodford", "Snaresbrook"} <= set(east_names)  # Epping branch
    assert {"Wanstead", "Gants Hill", "Newbury Park", "Fairlop", "Hainault"} <= set(east_names)  # via Newbury Park
    assert not {"Grange Hill", "Chigwell", "Roding Valley"} & set(east_names)  # NOT via Woodford
    assert west.severity_class == "minor" and west.is_unplanned
    assert west.started_at == datetime(2026, 9, 23, 15, 25, 24, tzinfo=timezone.utc)


# ---- section expansion ----------------------------------------------------------------


def test_northern_suspension_expands_to_all_ten_stations(net):
    inc, _ = one(net, status("northern", "Northern Line: No service between Stockwell and Morden due to a signal failure.", code=3))
    assert names(net, inc) == sorted(["Stockwell", "Clapham North", "Clapham Common", "Clapham South", "Balham",
                                      "Tooting Bec", "Tooting Broadway", "Colliers Wood", "South Wimbledon", "Morden"])
    assert inc.severity_class == "part_suspended"


def test_district_branch_is_followed_not_the_neighbouring_branch(net):
    inc, _ = one(net, status("district", "Severe delays between Earl's Court and Wimbledon.", code=6))
    got = set(names(net, inc))
    assert {"Earl's Court", "Parsons Green", "Southfields", "Wimbledon"} <= got
    assert not {"Kew Gardens", "Richmond", "Kensington (Olympia)", "Ealing Broadway"} & got


def test_station_names_containing_and(net):
    inc, _ = one(net, status("bakerloo", "Minor delays between Elephant and Castle and Oxford Circus."))
    assert {"Elephant & Castle", "Waterloo", "Oxford Circus"} <= set(names(net, inc))


def test_st_abbreviation_does_not_end_the_clause(net):
    inc, problems = one(net, status("central", "Minor delays between Bank and St. Paul's due to a signal failure."))
    assert names(net, inc) == ["Bank", "St. Paul's"] and problems == []


def test_elizabeth_line_same_place_platforms(net):
    inc, problems = one(net, status("elizabeth", "Severe delays between Paddington and Liverpool Street.", code=6))
    got = set(names(net, inc))
    assert {"Paddington", "Bond Street", "Tottenham Court Road", "Farringdon", "Liverpool Street"} <= got
    assert problems == []


def test_running_section_is_not_mistaken_for_the_disruption(net):
    inc, _ = one(net, status("northern", "Part suspended. Service operating between Morden and Kennington only.", code=3))
    assert inc.line_wide and inc.station_ids == ()


# ---- fallbacks ------------------------------------------------------------------------


def test_unplaceable_section_falls_back_to_whole_line_at_half_severity(net):
    inc, problems = one(net, status("victoria", "Severe delays between Nowhere Junction and Brixton.", code=6))
    assert inc.area_uncertain and inc.severity_scale == 0.5 and not inc.line_wide
    assert set(inc.station_ids) == set(net.lines["victoria"].station_ids)
    assert problems and "Nowhere Junction" in problems[0]


def test_no_section_means_line_wide(net):
    inc, problems = one(net, status("jubilee", "Jubilee Line: Severe delays due to an earlier fire alert.", code=6))
    assert inc.line_wide and inc.station_ids == () and problems == []


def test_single_station_closure(net):
    inc, _ = one(net, status("piccadilly", "Trains are not stopping at Covent Garden station due to overcrowding.", code=9))
    assert names(net, inc) == ["Covent Garden"]


def test_affected_stops_take_priority_over_text(net):
    inc, _ = one(net, status("victoria", "Severe delays between Brixton and Walthamstow Central.", code=6,
                             affected=[{"naptanId": "940GZZLUVXL"}]))
    assert names(net, inc) == ["Vauxhall"]


# ---- severity and planning ------------------------------------------------------------


@pytest.mark.parametrize("code", [10, 18, 19, 20])
def test_non_disruption_codes_are_ignored(net, code):
    assert parse_status(status("victoria", "Good service", code=code), net).incidents == []


@pytest.mark.parametrize("code,expected", [(2, "suspended"), (3, "part_suspended"), (6, "severe"), (9, "minor"), (8, "part_suspended")])
def test_severity_codes(net, code, expected):
    inc, _ = one(net, status("victoria", "Problem between Brixton and Stockwell.", code=code))
    assert inc.severity_class == expected


def test_unknown_code_is_minor_and_reported(net):
    inc, problems = one(net, status("victoria", "Something new between Brixton and Stockwell.", code=99))
    assert inc.severity_class == "minor" and "unknown statusSeverity 99" in problems[0]


def test_planned_work_category(net):
    inc, _ = one(net, status("victoria", "Closed between Brixton and Stockwell.", code=4, category="PlannedWork"))
    assert not inc.is_unplanned and inc.severity_class == "suspended"


def test_missing_category_uses_code_default(net):
    planned, _ = one(net, status("victoria", "Planned closure between Brixton and Stockwell.", code=4, category=None))
    unplanned, _ = one(net, status("victoria", "Severe delays between Brixton and Stockwell.", code=6, category=None))
    assert not planned.is_unplanned and unplanned.is_unplanned


# ---- keys -----------------------------------------------------------------------------


def test_key_is_stable_and_ignores_severity_and_wording(net):
    a, _ = one(net, status("victoria", "Minor delays between Brixton and Stockwell.", code=9))
    b, _ = one(net, status("victoria", "Severe delays between Brixton and Stockwell due to a faulty train.", code=6))
    c, _ = one(net, status("victoria", "Minor delays between Brixton and Vauxhall.", code=9))
    assert a.key == b.key != c.key


def test_duplicate_sections_keep_the_most_severe(net):
    payload = [{"id": "victoria", "lineStatuses": [
        status("victoria", "Minor delays between Brixton and Stockwell.", code=9)[0]["lineStatuses"][0],
        status("victoria", "Suspended between Brixton and Stockwell.", code=2)[0]["lineStatuses"][0],
    ]}]
    r = parse_status(payload, net)
    assert len(r.incidents) == 1 and r.incidents[0].severity_class == "suspended"


def test_unknown_line_is_skipped(net):
    assert parse_status(status("dlr", "Severe delays between A and B.", code=6), net).incidents == []
