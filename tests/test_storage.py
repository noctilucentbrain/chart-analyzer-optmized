import pandas as pd
import mongomock
from mongomock.filtering import filter_applies

from chart_analyzer.config import EventStrategyConfig
from chart_analyzer.storage import (
    MongoStorage,
    aggregate_candle_with_indicators,
    candle_documents,
    event_documents,
    indicator_collection_name,
    indicator_documents,
)


def test_candle_documents_shape():
    documents = candle_documents(
        pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "timeframe": "daily",
                    "timestamp": pd.Timestamp("2024-01-01", tz="UTC"),
                    "open": 1,
                    "high": 2,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 100,
                }
            ]
        )
    )

    assert documents == [
        {
            "ticker": "AAPL",
            "timeframe": "daily",
            "timestamp": pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100.0,
            "ohlc4": 1.25,
            "hlcc4": 1.375,
        }
    ]


def test_indicator_documents_shape():
    documents = indicator_documents(
        pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "timeframe": "daily",
                    "indicator": "rsi",
                    "timestamp": pd.Timestamp("2024-01-01", tz="UTC"),
                    "values": {"rsi": 55.5},
                }
            ]
        )
    )

    assert documents[0]["ticker"] == "AAPL"
    assert documents[0]["indicator"] == "rsi"
    assert documents[0]["values"] == {"rsi": 55.5}


def test_event_documents_shape():
    timestamp = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    documents = event_documents(
        [
            {
                "strategy": "position_entry",
                "ticker": "AAPL",
                "timeframe": "daily",
                "timestamp": timestamp,
                "operator": "AND",
                "conditions": {"rsi_between": True},
                "triggered": True,
            }
        ]
    )

    assert documents == [
        {
            "strategy": "position_entry",
            "ticker": "AAPL",
            "timeframe": "daily",
            "timestamp": timestamp,
            "operator": "AND",
            "conditions": {"rsi_between": True},
            "triggered": True,
        }
    ]


def test_indicator_collection_name_is_per_indicator():
    assert indicator_collection_name("rsi") == "indicator_rsi"
    assert indicator_collection_name("ema_50") == "indicator_ema_50"
    assert indicator_collection_name("bollinger_bands") == "indicator_bollinger_bands"
    assert indicator_collection_name("volume_at_price") == "indicator_volume_at_price"
    assert indicator_collection_name("price_stddev") == "indicator_price_stddev"
    assert indicator_collection_name("support resistance") == "indicator_support_resistance"


def test_aggregate_candle_with_indicators_shape():
    candle = {
        "_id": "database-id",
        "ticker": "AAPL",
        "timeframe": "daily",
        "timestamp": pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100.0,
    }
    indicators = [
        {"indicator": "rsi", "values": {"rsi": 55.5}},
        {"indicator": "ema_50", "values": {"ema": 123.4, "window": 50}},
    ]

    aggregated = aggregate_candle_with_indicators(candle, indicators)

    assert "_id" not in aggregated
    assert aggregated["ticker"] == "AAPL"
    assert aggregated["indicators"] == {
        "rsi": {"rsi": 55.5},
        "ema_50": {"ema": 123.4, "window": 50},
    }


def test_get_candle_with_indicators_reads_per_indicator_collections():
    timestamp = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    storage = MongoStorage.__new__(MongoStorage)
    storage.candles = FakeCollection(
        {
            "ticker": "AAPL",
            "timeframe": "daily",
            "timestamp": timestamp,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100.0,
        }
    )
    storage.database = FakeDatabase(
        {
            "indicator_rsi": FakeCollection(
                {
                    "ticker": "AAPL",
                    "timeframe": "daily",
                    "timestamp": timestamp,
                    "indicator": "rsi",
                    "values": {"rsi": 55.5},
                }
            ),
            "indicator_ema_50": FakeCollection(
                {
                    "ticker": "AAPL",
                    "timeframe": "daily",
                    "timestamp": timestamp,
                    "indicator": "ema_50",
                    "values": {"ema": 123.4, "window": 50},
                }
            ),
        }
    )

    aggregated = storage.get_candle_with_indicators("aapl", "Daily", timestamp)

    assert aggregated["ticker"] == "AAPL"
    assert aggregated["indicators"] == {
        "ema_50": {"ema": 123.4, "window": 50},
        "rsi": {"rsi": 55.5},
    }


def test_get_latest_candle_timestamp_returns_newest_timestamp():
    timestamp_1 = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    timestamp_2 = pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime()
    storage = MongoStorage.__new__(MongoStorage)
    storage.candles = FakeCollection([candle_doc(timestamp_1), candle_doc(timestamp_2)])

    assert storage.get_latest_candle_timestamp("aapl", "Daily") == timestamp_2


def test_get_latest_candles_with_events_returns_latest_candle_per_ticker():
    timestamp_1 = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    timestamp_2 = pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime()
    storage = MongoStorage.__new__(MongoStorage)
    storage.candles = FakeCollection(
        [
            candle_doc(timestamp_1),
            candle_doc(timestamp_2),
            {**candle_doc(timestamp_1), "ticker": "MSFT"},
        ]
    )
    storage.events = FakeCollection(
        [
            {
                "strategy": "position_entry",
                "ticker": "AAPL",
                "timeframe": "daily",
                "timestamp": timestamp_2,
                "operator": "AND",
                "conditions": {"rsi_between": True},
                "triggered": True,
            },
            {
                "strategy": "position_entry",
                "ticker": "AAPL",
                "timeframe": "daily",
                "timestamp": timestamp_1,
                "operator": "AND",
                "conditions": {"rsi_between": True},
                "triggered": True,
            },
            {
                "strategy": "position_exit",
                "ticker": "AAPL",
                "timeframe": "daily",
                "timestamp": timestamp_1,
                "operator": "OR",
                "conditions": {"macd_cross_under": True},
                "triggered": True,
            },
        ]
    )
    storage.database = FakeDatabase(
        {
            "indicator_rsi": FakeCollection(
                [
                    indicator_doc(timestamp_1, "rsi", {"rsi": 50}),
                    indicator_doc(timestamp_2, "rsi", {"rsi": 55}),
                    {**indicator_doc(timestamp_1, "rsi", {"rsi": 60}), "ticker": "MSFT"},
                ]
            )
        }
    )

    snapshot = storage.get_latest_candles_with_events("Daily")

    assert snapshot["timeframe"] == "daily"
    assert [(candle["ticker"], candle["timestamp"]) for candle in snapshot["candles"]] == [
        ("AAPL", timestamp_2),
        ("MSFT", timestamp_1),
    ]
    assert snapshot["candles"][0]["indicators"] == {"rsi": {"rsi": 55}}
    assert [(event["strategy"], event["ticker"], event["timestamp"]) for event in snapshot["events"]] == [
        ("position_entry", "AAPL", timestamp_2),
        ("position_exit", "AAPL", timestamp_1),
    ]


def test_detect_events_reads_candles_and_indicator_collections():
    timestamp_1 = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    timestamp_2 = pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime()
    strategy = EventStrategyConfig(
        name="position_entry",
        timeframe="daily",
        operator="AND",
        conditions={
            "macd_cross_over": {},
            "ema50_above_ema200": {},
            "rsi_between": {"min": 30, "max": 70},
            "volume_price_up": {"min_relative_volume": 0.9, "direction": "up"},
        },
    )
    storage = MongoStorage.__new__(MongoStorage)
    storage.candles = FakeCollection(
        [
            candle_doc(timestamp_1),
            candle_doc(timestamp_2),
        ]
    )
    storage.database = FakeDatabase(
        {
            "indicator_macd": FakeCollection(
                [
                    indicator_doc(timestamp_1, "macd", {"macd": 0.5, "signal": 0.7}),
                    indicator_doc(timestamp_2, "macd", {"macd": 0.8, "signal": 0.7}),
                ]
            ),
            "indicator_ema_50": FakeCollection(
                [
                    indicator_doc(timestamp_1, "ema_50", {"ema": 120}),
                    indicator_doc(timestamp_2, "ema_50", {"ema": 121}),
                ]
            ),
            "indicator_ema_200": FakeCollection(
                [
                    indicator_doc(timestamp_1, "ema_200", {"ema": 100}),
                    indicator_doc(timestamp_2, "ema_200", {"ema": 101}),
                ]
            ),
            "indicator_rsi": FakeCollection(
                [
                    indicator_doc(timestamp_1, "rsi", {"rsi": 50}),
                    indicator_doc(timestamp_2, "rsi", {"rsi": 55}),
                ]
            ),
            "indicator_volume_price_analysis": FakeCollection(
                [
                    indicator_doc(
                        timestamp_1,
                        "volume_price_analysis",
                        {"relative_volume": 1.0, "direction": "up"},
                    ),
                    indicator_doc(
                        timestamp_2,
                        "volume_price_analysis",
                        {"relative_volume": 1.1, "direction": "up"},
                    ),
                ]
            ),
        }
    )

    events = storage.detect_events(strategy)

    assert len(events) == 1
    assert events[0]["strategy"] == "position_entry"
    assert events[0]["timestamp"] == timestamp_2


def test_detect_events_can_save_to_events_collection():
    timestamp_1 = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    timestamp_2 = pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime()
    strategy = EventStrategyConfig(
        name="position_entry",
        timeframe="daily",
        operator="AND",
        conditions={
            "macd_cross_over": {},
            "ema50_above_ema200": {},
            "rsi_between": {"min": 30, "max": 70},
            "volume_price_up": {"min_relative_volume": 0.9, "direction": "up"},
        },
    )
    storage = MongoStorage.__new__(MongoStorage)
    storage.events = FakeCollection([])
    storage.candles = FakeCollection([candle_doc(timestamp_1), candle_doc(timestamp_2)])
    storage.database = FakeDatabase(
        {
            "indicator_macd": FakeCollection(
                [
                    indicator_doc(timestamp_1, "macd", {"macd": 0.5, "signal": 0.7}),
                    indicator_doc(timestamp_2, "macd", {"macd": 0.8, "signal": 0.7}),
                ]
            ),
            "indicator_ema_50": FakeCollection(
                [
                    indicator_doc(timestamp_1, "ema_50", {"ema": 120}),
                    indicator_doc(timestamp_2, "ema_50", {"ema": 121}),
                ]
            ),
            "indicator_ema_200": FakeCollection(
                [
                    indicator_doc(timestamp_1, "ema_200", {"ema": 100}),
                    indicator_doc(timestamp_2, "ema_200", {"ema": 101}),
                ]
            ),
            "indicator_rsi": FakeCollection(
                [
                    indicator_doc(timestamp_1, "rsi", {"rsi": 50}),
                    indicator_doc(timestamp_2, "rsi", {"rsi": 55}),
                ]
            ),
            "indicator_volume_price_analysis": FakeCollection(
                [
                    indicator_doc(
                        timestamp_1,
                        "volume_price_analysis",
                        {"relative_volume": 1.0, "direction": "up"},
                    ),
                    indicator_doc(
                        timestamp_2,
                        "volume_price_analysis",
                        {"relative_volume": 1.1, "direction": "up"},
                    ),
                ]
            ),
        }
    )

    events = storage.detect_events(strategy, save=True)

    assert len(events) == 1
    assert storage.events.documents == [event_documents(events)[0]]


def test_detect_events_after_only_saves_newer_events():
    timestamp_1 = pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime()
    timestamp_2 = pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime()
    strategy = EventStrategyConfig(
        name="position_entry",
        timeframe="daily",
        operator="AND",
        conditions={
            "macd_cross_over": {},
            "ema50_above_ema200": {},
            "rsi_between": {"min": 30, "max": 70},
            "volume_price_up": {"min_relative_volume": 0.9, "direction": "up"},
        },
    )
    storage = MongoStorage.__new__(MongoStorage)
    storage.events = FakeCollection([])
    storage.candles = FakeCollection([candle_doc(timestamp_1), candle_doc(timestamp_2)])
    storage.database = FakeDatabase(
        {
            "indicator_macd": FakeCollection(
                [
                    indicator_doc(timestamp_1, "macd", {"macd": 0.5, "signal": 0.7}),
                    indicator_doc(timestamp_2, "macd", {"macd": 0.8, "signal": 0.7}),
                ]
            ),
            "indicator_ema_50": FakeCollection(
                [
                    indicator_doc(timestamp_1, "ema_50", {"ema": 120}),
                    indicator_doc(timestamp_2, "ema_50", {"ema": 121}),
                ]
            ),
            "indicator_ema_200": FakeCollection(
                [
                    indicator_doc(timestamp_1, "ema_200", {"ema": 100}),
                    indicator_doc(timestamp_2, "ema_200", {"ema": 101}),
                ]
            ),
            "indicator_rsi": FakeCollection(
                [
                    indicator_doc(timestamp_1, "rsi", {"rsi": 50}),
                    indicator_doc(timestamp_2, "rsi", {"rsi": 55}),
                ]
            ),
            "indicator_volume_price_analysis": FakeCollection(
                [
                    indicator_doc(timestamp_1, "volume_price_analysis", {"relative_volume": 1.0, "direction": "up"}),
                    indicator_doc(timestamp_2, "volume_price_analysis", {"relative_volume": 1.1, "direction": "up"}),
                ]
            ),
        }
    )

    events = storage.detect_events(strategy, after=timestamp_2, save=True)

    assert events == []
    assert storage.events.documents == []


def candle_doc(timestamp):
    return {
        "ticker": "AAPL",
        "timeframe": "daily",
        "timestamp": timestamp,
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100.0,
    }


def indicator_doc(timestamp, indicator, values):
    return {
        "ticker": "AAPL",
        "timeframe": "daily",
        "timestamp": timestamp,
        "indicator": indicator,
        "values": values,
    }


class FakeCollection:
    def __init__(self, documents):
        if isinstance(documents, dict):
            documents = [documents]
        self.documents = documents

    def find_one(self, query, projection):
        for document in self.documents:
            if filter_applies(query, document):
                if projection == {"_id": 0}:
                    return {key: value for key, value in document.items() if key != "_id"}
                return document
        return None

    def find(self, query, projection):
        return FakeCursor(
            [
                {key: value for key, value in document.items() if key != "_id"}
                for document in self.documents
                if filter_applies(query, document)
            ]
        )

    def aggregate(self, pipeline):
        collection = mongomock.MongoClient(tz_aware=True).db.collection
        if self.documents:
            collection.insert_many([dict(d) for d in self.documents])
        return collection.aggregate(pipeline)

    def bulk_write(self, operations, ordered):
        upserted_ids = {}
        for index, operation in enumerate(operations):
            query = operation._filter
            document = operation._doc["$set"]
            for existing_index, existing in enumerate(self.documents):
                if all(existing.get(key) == value for key, value in query.items()):
                    self.documents[existing_index] = document
                    break
            else:
                self.documents.append(document)
                upserted_ids[index] = index
        return FakeBulkWriteResult(upserted_ids)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, keys):
        for key, direction in reversed(keys):
            self.documents.sort(key=lambda document: document[key], reverse=direction < 0)
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeBulkWriteResult:
    def __init__(self, upserted_ids):
        self.matched_count = 0
        self.modified_count = 0
        self.upserted_ids = upserted_ids


class FakeDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]

    def list_collection_names(self):
        return list(self.collections)
