from pymongo import MongoClient

# Requires the PyMongo package.
# https://api.mongodb.com/python/current

client = MongoClient('mongodb://100.120.206.81:27017/')
result = client['trading']['indicator_ema_200'].aggregate([
    {
        '$lookup': {
            'from': 'indicator_ema_50', 
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
                        'ema_50': '$values.ema'
                    }
                }
            ], 
            'as': 'ema_50_doc'
        }
    }, {
        '$unwind': {
            'path': '$ema_50_doc', 
            'preserveNullAndEmptyArrays': True
        }
    }, {
        '$project': {
            '_id': 0, 
            'ticker': 1, 
            'timeframe': 1, 
            'timestamp': 1, 
            'ema_50': '$ema_50_doc.ema_50', 
            'ema_200': '$values.ema'
        }
    }
])

for doc in result:
    print(doc)