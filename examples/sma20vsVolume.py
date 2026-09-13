from pymongo import MongoClient

# Requires the PyMongo package.
# https://api.mongodb.com/python/current

client = MongoClient('mongodb://100.120.206.81:27017/')
result = client['trading']['indicator_volume_sma'].aggregate([
    {
        '$lookup': {
            'from': 'candles', 
            'let': {
                'ticker': '$ticker', 
                'timeframe': '$timeframe', 
                'timestamp': '$timestamp'
            }, 
            'pipeline': [
                {
                    '$match': {
                        '$expr': {
                            '$and': [
                                {
                                    '$eq': [
                                        '$ticker', '$$ticker'
                                    ]
                                }, {
                                    '$eq': [
                                        '$timeframe', '$$timeframe'
                                    ]
                                }, {
                                    '$eq': [
                                        '$timestamp', '$$timestamp'
                                    ]
                                }
                            ]
                        }
                    }
                }, {
                    '$project': {
                        '_id': 0, 
                        'volume': 1
                    }
                }
            ], 
            'as': 'candle'
        }
    }, {
        '$unwind': {
            'path': '$candle', 
            'preserveNullAndEmptyArrays': True
        }
    }, {
        '$addFields': {
            'sma20': '$values.sma', 
            'volume': '$candle.volume'
        }
    }, {
        '$project': {
            'candle': 0
        }
    }
])

for doc in result:
    print(doc)