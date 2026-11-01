import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request
from flask_cors import CORS

from src import (
    TrainPageGetter,
    StationsGetter,
    StationTimetableGetter,
    config,
)

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = os.environ.get("API_KEY", "")
TZ = ZoneInfo("Europe/Bucharest")

STATION_NAMES = {
    "tecuci": ["tecuci"],
    "galati": ["galati", "galați"],
}


def authorized():
    return not API_KEY or request.headers.get("X-API-Key") == API_KEY


def find_station(stations, aliases):
    for station in stations:
        name = station.get("name", "").strip().lower()
        if any(alias in name for alias in aliases):
            return station
    return None


def get_stations():
    return StationsGetter.get_stations()


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "tecuci-galati-trains",
        "time": datetime.now(TZ).isoformat()
    })


@app.route("/api/stations")
def api_stations():
    if not authorized():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        return jsonify(get_stations())
    except Exception:
        logger.exception("Station lookup failed")
        return jsonify({"error": "Nu s-au putut prelua statiile IRIS"}), 502


@app.route("/api/trains")
def api_trains():
    if not authorized():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        stations = get_stations()

        tecuci = find_station(stations, STATION_NAMES["tecuci"])
        galati = find_station(stations, STATION_NAMES["galati"])

        if not tecuci or not galati:
            return jsonify({
                "error": "Nu am identificat ambele statii",
                "stations_found": [
                    s.get("name") for s in stations
                    if "tecuci" in s.get("name", "").lower()
                    or "gala" in s.get("name", "").lower()
                ]
            }), 502

        config.global_station_list = {
            s["name"]: s["station_id"] for s in stations
        }

        directions = [
            (tecuci, galati, "Tecuci → Galați"),
            (galati, tecuci, "Galați → Tecuci"),
        ]

        results = []
        now = datetime.now(TZ)
        end = now + timedelta(hours=24)

        for origin, destination, direction in directions:
            timetable = StationTimetableGetter.get_timetable(
                origin["station_id"]
            )

            candidates = [
                item for item in timetable
                if item.get("is_origin") or item.get("is_stop")
            ]

            for item in candidates[:30]:
                train_id = item.get("train_id")
                if not train_id:
                    continue

                try:
                    route = TrainPageGetter.get_train(str(train_id))
                except Exception:
                    logger.exception("Train detail failed: %s", train_id)
                    continue

                origin_stop = None
                destination_stop = None

                for stop in route:
                    name = stop.get("station_name", "").lower()

                    if "tecuci" in name:
                        origin_stop = stop
                    if "gala" in name:
                        destination_stop = stop

                if not origin_stop or not destination_stop:
                    continue

                origin_time = (
                    origin_stop.get("departure_timestamp")
                    or origin_stop.get("arrival_timestamp")
                )
                destination_time = (
                    destination_stop.get("arrival_timestamp")
                    or destination_stop.get("departure_timestamp")
                )

                if not origin_time or not destination_time:
                    continue

                try:
                    departure = datetime.fromisoformat(
                        origin_time.replace("Z", "+00:00")
                    )
                    arrival = datetime.fromisoformat(
                        destination_time.replace("Z", "+00:00")
                    )

                    if departure.tzinfo is None:
                        departure = departure.replace(tzinfo=TZ)
                    if arrival.tzinfo is None:
                        arrival = arrival.replace(tzinfo=TZ)

                    if not (now <= departure <= end):
                        continue

                    delay = None
                    if origin_stop.get("is_real_time"):
                        delay = origin_stop.get("delay")

                    results.append({
                        "train_id": str(train_id),
                        "direction": direction,
                        "origin": origin.get("name"),
                        "destination": destination.get("name"),
                        "departure": departure.isoformat(),
                        "arrival": arrival.isoformat(),
                        "delay_minutes": delay,
                        "status": (
                            "live" if origin_stop.get("is_real_time")
                            else "schedule"
                        ),
                        "source": "IRIS"
                    })

                except Exception:
                    logger.exception("Invalid train timestamps")

        unique = {}
        for train in results:
            unique[train["train_id"] + train["direction"]] = train

        output = sorted(
            unique.values(),
            key=lambda x: x["departure"]
        )

        return jsonify({
            "ok": True,
            "source": "IRIS",
            "updated_at": datetime.now(TZ).isoformat(),
            "trains": output
        })

    except Exception:
        logger.exception("Train retrieval failed")
        return jsonify({
            "ok": False,
            "error": "Nu s-au putut prelua datele IRIS"
        }), 502
