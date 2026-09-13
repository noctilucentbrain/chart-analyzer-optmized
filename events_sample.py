from chart_analyzer.config import load_config
from chart_analyzer.storage import MongoStorage
from bson.json_util import dumps, loads


config = load_config("examples/config.yaml")
storage = MongoStorage(config.mongodb.uri, config.mongodb.database)

events = storage.detect_events(config.events[0], ticker="AAPL", save=True)

json_string = dumps(events, indent=2)
print(json_string)

storage.close()