"""Small dependency-light regression checks for FulfillCore 0.7.9.0 reliability helpers.

Run on the server with:
    python app/tests/test_reliability_unit.py
"""
from app.services.runtime_reliability import normalize_device_id, normalize_station_code, station_token, verify_station_token


def main() -> None:
    assert normalize_station_code(" pack 01 ") == "PACK-01"
    assert normalize_station_code("PACK-02") == "PACK-02"
    assert normalize_station_code("") == "PACK-01"
    assert normalize_device_id(" device 01 ") == "device-01"
    master = "example-master-token"
    t1 = station_token(master, "PACK-01")
    t2 = station_token(master, "PACK-02")
    assert t1 != t2
    assert verify_station_token(master, "PACK-01", t1)
    assert not verify_station_token(master, "PACK-02", t1)
    print("OK: FulfillCore 0.7.9.0 reliability unit checks passed")


if __name__ == "__main__":
    main()
