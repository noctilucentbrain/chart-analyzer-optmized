from chart_analyzer.storage import MongoStorage
from bson.json_util import dumps, loads

client = Mongo

storage = MongoStorage("mongodb://localhost:27017", "trading")
document = storage.get_candle_with_indicators(
    ticker="AAPL",
    timeframe="daily",
    timestamp="2026-01-02T00:00:00Z",
)

json_string = dumps(document, indent=2)
print(json_string)
storage.close()