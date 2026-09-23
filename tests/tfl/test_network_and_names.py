import json
from pathlib import Path

from surgesignal.tfl.names import display_name, lookup_keys, normalise
from surgesignal.tfl.network import Network, build_network, load_network

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tfl"


def test_normalise_variants_meet():
    assert normalise("King's Cross St. Pancras") == normalise("Kings Cross St Pancras") == "kings cross st pancras"
    assert normalise("Elephant & Castle") == normalise("Elephant and Castle")
    assert normalise("Stockwell Underground Station") == "stockwell"


def test_lookup_keys_cover_brackets_and_london_prefix():
    assert {"edgware road circle line", "edgware road"} <= lookup_keys("Edgware Road (Circle Line)")
    assert {"london paddington", "paddington"} <= lookup_keys("London Paddington")
    assert "paddington" in lookup_keys("Paddington (H&C Line)-Underground")


def test_display_name():
    assert display_name("London Liverpool Street Rail Station") == "Liverpool Street"
    assert display_name("Kensington (Olympia) Underground Station") == "Kensington (Olympia)"


def test_build_from_fixtures_and_round_trip():
    seqs = [json.loads((FIXTURES / f).read_text()) for f in (
        "route_sequence_northern_outbound_2026-09-23.json",
        "route_sequence_central_outbound_2026-09-23.json",
        "route_sequence_district_outbound_2026-09-23.json",
    )]
    net = build_network(seqs)
    assert set(net.lines) == {"northern", "central", "district"}
    assert net.stations["940GZZLUSKW"].name == "Stockwell"
    assert Network.from_json(json.loads(json.dumps(net.to_json()))) == net


def test_committed_network_covers_all_lines():
    net = load_network()
    assert len(net.lines) == 12 and len(net.stations) > 300
    assert all(r.station_ids for line in net.lines.values() for r in line.routes)


def test_same_place_ids_share_a_name_but_distant_ones_would_not():
    net = load_network()
    assert net.name_index("circle")["paddington"] == ("940GZZLUPAC", "940GZZLUPAH")
    assert len(net.name_index("elizabeth")["liverpool street"]) == 2
    # no line has a genuinely ambiguous (far-apart) name today
    for line_id in net.lines:
        index = net.name_index(line_id)
        for sid in net.lines[line_id].station_ids:
            assert any(sid in ids for ids in index.values()), (line_id, net.stations[sid].name)
