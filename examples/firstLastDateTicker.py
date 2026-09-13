from pymongo import MongoClient

# Requires the PyMongo package.
# https://api.mongodb.com/python/current

client = MongoClient('mongodb://100.120.206.81:27017/')
result = client['trading']['candles'].aggregate([
    {
        '$match': {
            'ticker': 'AAPL'
        }
    }, {
        '$group': {
            '_id': None, 
            'firstRecordDate': {
                '$min': '$timestamp'
            }, 
            'lastRecordDate': {
                '$max': '$timestamp'
            }
        }
    }, {
        '$project': {
            '_id': 0, 
            'firstRecordDate': 1, 
            'lastRecordDate': 1
        }
    }
])