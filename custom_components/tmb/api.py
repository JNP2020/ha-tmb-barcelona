"""Thin async client for the TMB (api.tmb.cat/v1) public API.

TMB exposes several independent services under one base URL, all
authenticated the same way (an `app_id`/`app_key` query-string pair issued
by https://developer.tmb.cat/):

- `transit/linies/{bus,metro}` (+ `/parades` or `/estacions`): the static
  network catalog (lines, stops/stations, colors) as GeoJSON. Used only to
  populate the config/options flow dropdowns.
- `ibus/stops/{stop}`: real-time bus arrivals for a stop. Called *without* a
  line filter (TMB's own line-filtered variant is unreliable and frequently
  returns empty results regardless of the line passed), returning every line
  serving that stop; callers filter client-side.
- `itransit/metro/estacions`: real-time metro arrivals. The response is not
  scoped by the `estacions` query param the way you'd expect — it returns
  every line's data, so one call per station code covers every line serving
  that station, same as iBus.
- `planner/plan`: point-to-point multimodal trip planning.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, TypedDict

import aiohttp

from .const import API_BASE_URL


class TmbApiError(Exception):
    """Generic error talking to the TMB API."""


class TmbAuthError(TmbApiError):
    """The provided app_id/app_key was rejected."""


class LineInfo(TypedDict):
    code: str
    name: str
    color: str | None
    origin: str | None
    destination: str | None


class StopInfo(TypedDict):
    code: str
    name: str
    lat: float | None
    lon: float | None


class Arrival(TypedDict):
    line_code: str
    destination: str
    eta_sec: int


class Itinerary(TypedDict):
    duration_seconds: int
    walk_distance_meters: int
    transfers: int
    overview: str
    description: str


def _as_str(props: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = props.get(key)
        if value is not None:
            return str(value)
    return None


class TmbApiClient:
    """Async client for the TMB public API."""

    def __init__(self, session: aiohttp.ClientSession, app_id: str, app_key: str) -> None:
        self._session = session
        self._app_id = app_id
        self._app_key = app_key

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        request_params = {"app_id": self._app_id, "app_key": self._app_key, **(params or {})}
        try:
            async with self._session.get(url, params=request_params) as resp:
                if resp.status in (401, 403):
                    raise TmbAuthError("Invalid TMB app_id/app_key")
                if resp.status != 200:
                    body = await resp.text()
                    raise TmbApiError(f"TMB API returned HTTP {resp.status}: {body[:200]}")
                try:
                    return await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise TmbApiError(
                        f"TMB API returned an unparsable response: {err}"
                    ) from err
        except aiohttp.ClientError as err:
            raise TmbApiError(f"Error communicating with TMB API: {err}") from err

    # ------------------------------------------------------------------
    # Static catalog (transit/linies/*) — used by the config/options flow.
    # ------------------------------------------------------------------

    async def async_get_bus_lines(self) -> list[LineInfo]:
        payload = await self._get(f"{API_BASE_URL}/transit/linies/bus")
        lines = []
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            code = _as_str(props, ["CODI_LINIA", "codi_linia"])
            name = _as_str(props, ["NOM_LINIA", "nom_linia"])
            if not code or not name:
                continue
            lines.append(
                LineInfo(
                    code=code,
                    name=name,
                    color=_as_str(props, ["COLOR_LINIA", "color_linia"]),
                    origin=_as_str(props, ["ORIGEN_LINIA", "origen_linia"]),
                    destination=_as_str(props, ["DESTI_LINIA", "desti_linia"]),
                )
            )
        lines.sort(key=lambda line: line["name"])
        return lines

    async def async_get_bus_line_stops(self, line_code: str) -> list[StopInfo]:
        payload = await self._get(
            f"{API_BASE_URL}/transit/linies/bus/{line_code}/parades"
        )
        return self._parse_stops(payload, code_keys=["CODI_PARADA", "codi_parada"])

    async def async_get_metro_lines(self) -> list[LineInfo]:
        payload = await self._get(f"{API_BASE_URL}/transit/linies/metro")
        lines = []
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            code = _as_str(props, ["CODI_LINIA", "codi_linia"])
            name = _as_str(props, ["NOM_LINIA", "nom_linia"])
            if not code:
                continue
            lines.append(
                LineInfo(
                    code=code,
                    name=name or f"L{code}",
                    color=_as_str(props, ["COLOR_LINIA", "color_linia"]),
                    origin=_as_str(props, ["ORIGEN_LINIA", "origen_linia"]),
                    destination=_as_str(props, ["DESTI_LINIA", "desti_linia"]),
                )
            )
        lines.sort(key=lambda line: line["name"])
        return lines

    async def async_get_metro_line_stations(self, line_code: str) -> list[StopInfo]:
        payload = await self._get(
            f"{API_BASE_URL}/transit/linies/metro/{line_code}/estacions"
        )
        return self._parse_stops(
            payload, code_keys=["CODI_ESTACIO", "codi_estacio", "CODI_GRUP_ESTACIO"]
        )

    @staticmethod
    def _parse_stops(payload: dict[str, Any], code_keys: list[str]) -> list[StopInfo]:
        stops: list[StopInfo] = []
        seen: set[str] = set()
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            code = _as_str(props, code_keys)
            if not code or code in seen:
                continue
            seen.add(code)
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            lon, lat = (coords[0], coords[1]) if coords and len(coords) == 2 else (None, None)
            stops.append(
                StopInfo(
                    code=code,
                    name=_as_str(props, ["NOM_PARADA", "nom_parada", "NOM_ESTACIO", "nom_estacio"])
                    or f"#{code}",
                    lat=lat,
                    lon=lon,
                )
            )
        stops.sort(key=lambda stop: stop["name"])
        return stops

    # ------------------------------------------------------------------
    # Real-time arrivals.
    # ------------------------------------------------------------------

    async def async_get_bus_arrivals(self, stop_code: str) -> list[Arrival]:
        payload = await self._get(f"{API_BASE_URL}/ibus/stops/{stop_code}")
        entries = (payload.get("data") or {}).get("ibus") or []
        arrivals: list[Arrival] = []
        for entry in entries:
            line = entry.get("line")
            eta_min = entry.get("t-in-min")
            eta_sec = entry.get("t-in-s")
            if line is None or (eta_min is None and eta_sec is None):
                continue
            destination = (
                entry.get("destination")
                or entry.get("text-en")
                or entry.get("text-ca")
                or "?"
            )
            arrivals.append(
                Arrival(
                    line_code=str(line),
                    destination=str(destination),
                    eta_sec=int(eta_sec) if eta_sec is not None else int(eta_min) * 60,
                )
            )
        arrivals.sort(key=lambda arrival: arrival["eta_sec"])
        return arrivals

    async def async_get_metro_arrivals(self, station_code: str) -> list[Arrival]:
        """Fetch real-time metro arrivals for a station.

        `temps_arribada` is an absolute epoch-millisecond timestamp (matching
        the response's own `timestamp` field), not a relative countdown, so
        it has to be converted to "seconds from now" against the current
        wall-clock time.
        """
        payload = await self._get(
            f"{API_BASE_URL}/itransit/metro/estacions", {"estacions": station_code}
        )
        now_ms = time.time() * 1000
        arrivals: list[Arrival] = []
        for line in payload.get("linies") or []:
            line_code = line.get("codi_linia")
            if line_code is None:
                continue
            for station in line.get("estacions") or []:
                if str(station.get("codi_estacio")) != str(station_code):
                    continue
                for path in station.get("linies_trajectes") or []:
                    destination = path.get("desti_trajecte") or "?"
                    for train in path.get("propers_trens") or []:
                        arrival_ms = train.get("temps_arribada")
                        if not isinstance(arrival_ms, (int, float)):
                            continue
                        arrivals.append(
                            Arrival(
                                line_code=str(line_code),
                                destination=str(destination),
                                eta_sec=max(0, int((arrival_ms - now_ms) / 1000)),
                            )
                        )
        arrivals.sort(key=lambda arrival: arrival["eta_sec"])
        return arrivals

    # ------------------------------------------------------------------
    # Trip planner.
    # ------------------------------------------------------------------

    async def async_get_itineraries(
        self, from_lat: float, from_lon: float, to_lat: float, to_lon: float
    ) -> list[Itinerary]:
        now = datetime.now()
        payload = await self._get(
            f"{API_BASE_URL}/planner/plan",
            {
                "fromPlace": f"{from_lat},{from_lon}",
                "toPlace": f"{to_lat},{to_lon}",
                "date": now.strftime("%m-%d-%Y"),
                "time": now.strftime("%I:%M%p"),
                "arriveBy": "false",
                "mode": "TRANSIT,WALK",
            },
        )
        plan = payload.get("plan") or {}
        itineraries: list[Itinerary] = []
        for it in plan.get("itineraries", []):
            legs = [leg for leg in it.get("legs", []) if leg.get("mode") != "WALK"]
            overview = ", ".join(leg.get("route", "?") for leg in legs)
            description = ", ".join(
                f"{leg.get('route', '?')} ({leg.get('from', {}).get('name')} -> "
                f"{leg.get('to', {}).get('name')})"
                for leg in legs
            )
            itineraries.append(
                Itinerary(
                    duration_seconds=int(it.get("duration", 0)),
                    walk_distance_meters=round(it.get("walkDistance", 0)),
                    transfers=int(it.get("transfers", 0)),
                    overview=overview,
                    description=description,
                )
            )
        return itineraries
