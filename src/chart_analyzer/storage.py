from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from threading import Lock

import pandas as pd
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.collection import Collection

from chart_analyzer.config import EventStrategyConfig
from chart_analyzer.events import detect_events_for_candles, required_indicators


@dataclass(frozen=True)
class UpsertResult:
    matched: int
    modified: int
    upserted: int


class MongoStorage:
    def __init__(self, uri: str, database: str, server_selection_timeout_ms: int = 5000) -> None:
        self.client: MongoClient[dict[str, Any]] = MongoClient(
            uri,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
        )
        self.database = self.client[database]
        self.candles = self.database["candles"]
        self.events = self.database["events"]
        self._indexed_indicators: set[str] = set()
        self._index_lock = Lock()

    def ensure_indexes(self) -> None:
        self.candles.create_index(
            [("ticker", ASCENDING), ("timeframe", ASCENDING), ("timestamp", ASCENDING)],
            unique=True,
            name="unique_candle",
        )
        self.events.create_index(
            [("strategy", ASCENDING), ("ticker", ASCENDING), ("timeframe", ASCENDING), ("timestamp", ASCENDING)],
            unique=True,
            name="unique_event",
        )

        self.candles.create_index(
            [("timeframe", ASCENDING), ("ticker", ASCENDING), ("timestamp", DESCENDING)],
            name="latest_candles_by_timeframe",
        )
        self.events.create_index(
            [("timeframe", ASCENDING), ("ticker", ASCENDING), ("strategy", ASCENDING), ("timestamp", DESCENDING)],
            name="latest_events_by_timeframe",
        )

        self.database.session_evaluations.create_index(
            [("triggered", ASCENDING), ("expires_at", ASCENDING)], name="actionable_session_events")

    def get_actionable_events(self, timeframe=None, ticker=None, now=None):
        from chart_analyzer.sessions import utc, utc_now
        now = utc(now) if now is not None else utc_now()
        query = {"triggered": True, "evaluated_at": {"$lte": now.to_pydatetime()},
                 "expires_at": {"$gt": now.to_pydatetime()}, "session_close": {"$gt": now.to_pydatetime()}}
        if timeframe is not None:
            query['timeframe'] = timeframe.lower()
        if ticker is not None:
            query['ticker'] = ticker.upper()
        return list(self.database.session_evaluations.find(query, {'_id': 0}).sort([
            ('evaluated_at', ASCENDING), ('event_id', ASCENDING)]))

    def upsert_candles(self, candles: pd.DataFrame) -> UpsertResult:
        return _bulk_upsert(
            self.candles,
            candle_documents(candles),
            ["ticker", "timeframe", "timestamp"],
        )

    def upsert_indicators(self, indicators: pd.DataFrame) -> UpsertResult:
        totals = UpsertResult(matched=0, modified=0, upserted=0)
        if indicators.empty:
            return totals

        for indicator, group in indicators.groupby("indicator"):
            collection = self._indicator_collection(str(indicator))
            result = _bulk_upsert(
                collection,
                indicator_documents(group),
                ["ticker", "timeframe", "timestamp"],
            )
            totals = UpsertResult(
                matched=totals.matched + result.matched,
                modified=totals.modified + result.modified,
                upserted=totals.upserted + result.upserted,
            )
        return totals

    def get_candle_with_indicators(
        self,
        ticker: str,
        timeframe: str,
        timestamp: datetime | pd.Timestamp | str,
        indicators: list[str] | None = None,
    ) -> dict[str, Any] | None:
        query = {
            "ticker": ticker.upper(),
            "timeframe": timeframe.lower(),
            "timestamp": _to_query_datetime(timestamp),
        }
        candle = self.candles.find_one(query, {"_id": 0})
        if candle is None:
            return None

        indicator_names = indicators or self._stored_indicator_names()
        indicator_documents_for_candle = []
        for indicator in indicator_names:
            document = self.database[indicator_collection_name(indicator)].find_one(query, {"_id": 0})
            if document is not None:
                indicator_documents_for_candle.append(document)

        return aggregate_candle_with_indicators(candle, indicator_documents_for_candle)

    def get_latest_candle_timestamp(
        self,
        ticker: str,
        timeframe: str,
    ) -> datetime | None:
        query = {
            "ticker": ticker.upper(),
            "timeframe": timeframe.lower(),
        }
        for candle in self.candles.find(query, {"_id": 0, "timestamp": 1}).sort([("timestamp", DESCENDING)]).limit(1):
            return candle["timestamp"]
        return None

    def get_latest_candles_with_events(
        self,
        timeframe: str,
        ticker: str | None = None,
        indicators: list[str] | None = None,
    ) -> dict[str, Any]:
        query = {"timeframe": timeframe.lower()}
        if ticker is not None:
            query["ticker"] = ticker.upper()

        candles = list(self.candles.aggregate([
            {"$match": query},
            {"$sort": {"ticker": 1, "timestamp": -1}},
            {"$group": {"_id": "$ticker", "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$project": {"_id": 0}},
            {"$sort": {"ticker": 1}},
        ]))
        events = list(self.events.aggregate([
            {"$match": query},
            {"$sort": {"ticker": 1, "strategy": 1, "timestamp": -1}},
            {"$group": {"_id": {"ticker": "$ticker", "strategy": "$strategy"}, "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$project": {"_id": 0}},
            {"$sort": {"ticker": 1, "strategy": 1}},
        ]))
        return {
            "timeframe": timeframe.lower(),
            "candles": self._join_indicators(candles, indicators or self._stored_indicator_names()),
            "events": events,
        }

    def _join_indicators(self, candles: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
        # Exact keys avoid fetching other series or historical indicator documents.
        result = []
        for offset in range(0, len(candles), 500):
            batch = [aggregate_candle_with_indicators(candle, []) for candle in candles[offset:offset + 500]]
            by_key = {(c["ticker"], c["timeframe"], c["timestamp"]): c for c in batch}
            query = {"$or": [{"ticker": k[0], "timeframe": k[1], "timestamp": k[2]} for k in by_key]}
            for name in names:
                for doc in self.database[indicator_collection_name(name)].find(query, {"_id": 0}):
                    key = (doc["ticker"], doc["timeframe"], doc["timestamp"])
                    by_key[key]["indicators"][name] = doc.get("values", {})
            result.extend(batch)
        return result

    def detect_events(
        self,
        strategy: EventStrategyConfig,
        ticker: str | None = None,
        timeframe: str | None = None,
        after: datetime | pd.Timestamp | str | None = None,
        save: bool = False,
    ) -> list[dict[str, Any]]:
        query = {}
        if ticker is not None:
            query["ticker"] = ticker.upper()
        selected_timeframe = timeframe or strategy.timeframe
        if selected_timeframe is not None:
            query["timeframe"] = selected_timeframe.lower()

        # A cross-over needs exactly one predecessor for each series. The normal
        # ingestion path specifies both dimensions, so it never scans old history.
        predecessor = []
        if after is not None and ticker is not None and selected_timeframe is not None:
            boundary = _to_query_datetime(after)
            predecessor = list(self.candles.find(
                {**query, "timestamp": {"$lte": boundary}}, {"_id": 0}
            ).sort([("timestamp", DESCENDING)]).limit(1))
            query["timestamp"] = {"$gt": boundary}
        candles = list(self.candles.find(query, {"_id": 0}).sort(
            [("ticker", ASCENDING), ("timeframe", ASCENDING), ("timestamp", ASCENDING)]
        ))
        candles = self._join_indicators(predecessor + candles, required_indicators(strategy))

        events = detect_events_for_candles(candles, strategy)
        if after is not None:
            after_timestamp = _to_query_datetime(after)
            events = [event for event in events if _to_query_datetime(event["timestamp"]) > after_timestamp]
        if save:
            self.upsert_events(events)
        return events

    def upsert_events(self, events: list[dict[str, Any]]) -> UpsertResult:
        return _bulk_upsert(
            self.events,
            event_documents(events),
            ["strategy", "ticker", "timeframe", "timestamp"],
        )

    def close(self) -> None:
        self.client.close()

    def _indicator_collection(self, indicator: str) -> Collection[dict[str, Any]]:
        collection = self.database[indicator_collection_name(indicator)]
        with self._index_lock:
            if indicator not in self._indexed_indicators:
                collection.create_index(
                    [("ticker", ASCENDING), ("timeframe", ASCENDING), ("timestamp", ASCENDING)],
                    unique=True,
                    name="unique_indicator_timestamp",
                )
                self._indexed_indicators.add(indicator)
        return collection

    def _stored_indicator_names(self) -> list[str]:
        prefix = "indicator_"
        return sorted(
            collection.removeprefix(prefix)
            for collection in self.database.list_collection_names()
            if collection.startswith(prefix)
        )


def candle_documents(candles: pd.DataFrame) -> list[dict[str, Any]]:
    documents = []
    for row in candles.to_dict(orient="records"):
        documents.append(
            {
                "ticker": row["ticker"],
                "timeframe": row["timeframe"],
                "timestamp": _to_python_datetime(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "ohlc4": _row_ohlc4(row),
                "hlcc4": _row_hlcc4(row),
            }
        )
    return documents


def indicator_documents(indicators: pd.DataFrame) -> list[dict[str, Any]]:
    documents = []
    for row in indicators.to_dict(orient="records"):
        documents.append(
            {
                "ticker": row["ticker"],
                "timeframe": row["timeframe"],
                "indicator": row["indicator"],
                "timestamp": _to_python_datetime(row["timestamp"]),
                "values": row["values"],
            }
        )
    return documents


def event_documents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents = []
    for event in events:
        documents.append(
            {
                "strategy": event["strategy"],
                "ticker": event["ticker"],
                "timeframe": event["timeframe"],
                "timestamp": _to_python_datetime(event["timestamp"]),
                "operator": event["operator"],
                "conditions": event["conditions"],
                "triggered": bool(event["triggered"]),
            }
        )
    return documents


def aggregate_candle_with_indicators(
    candle: dict[str, Any],
    indicators: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregated = dict(candle)
    aggregated.pop("_id", None)
    aggregated["indicators"] = {}

    for indicator in indicators:
        name = indicator["indicator"]
        aggregated["indicators"][name] = indicator.get("values", {})

    return aggregated


def indicator_collection_name(indicator: str) -> str:
    normalized = indicator.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized or not all(character.isalnum() or character == "_" for character in normalized):
        raise ValueError(f"Invalid indicator name for MongoDB collection: {indicator}")
    return f"indicator_{normalized}"


def _bulk_upsert(
    collection: Collection[dict[str, Any]],
    documents: list[dict[str, Any]],
    keys: list[str],
) -> UpsertResult:
    if not documents:
        return UpsertResult(matched=0, modified=0, upserted=0)

    matched = modified = upserted = 0
    for offset in range(0, len(documents), 1000):
        operations = [UpdateOne({key: doc[key] for key in keys}, {"$set": doc}, upsert=True)
                      for doc in documents[offset:offset + 1000]]
        result = collection.bulk_write(operations, ordered=False)
        matched += result.matched_count
        modified += result.modified_count
        upserted += len(result.upserted_ids)
    return UpsertResult(matched=matched, modified=modified, upserted=upserted)


def _to_python_datetime(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _row_ohlc4(row: dict[str, Any]) -> float:
    if "ohlc4" in row and pd.notna(row["ohlc4"]):
        return float(row["ohlc4"])
    return (float(row["open"]) + float(row["high"]) + float(row["low"]) + float(row["close"])) / 4


def _row_hlcc4(row: dict[str, Any]) -> float:
    if "hlcc4" in row and pd.notna(row["hlcc4"]):
        return float(row["hlcc4"])
    return (float(row["high"]) + float(row["low"]) + float(row["close"]) + float(row["close"])) / 4


def _to_query_datetime(value: datetime | pd.Timestamp | str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()
