from pymongo import MongoClient

# Requires the PyMongo package.
# https://api.mongodb.com/python/current

client = MongoClient('mongodb://admin:admin@100.120.206.81:27017/?authSource=trading')
result = client['trading']['indicator_macd'].aggregate([
    {
        '$setWindowFields': {
            'partitionBy': {
                'ticker': '$ticker', 
                'timeframe': '$timeframe'
            }, 
            'sortBy': {
                'timestamp': 1
            }, 
            'output': {
                'prevMacd': {
                    '$shift': {
                        'output': '$values.macd', 
                        'by': -1
                    }
                }, 
                'prevSignal': {
                    '$shift': {
                        'output': '$values.signal', 
                        'by': -1
                    }
                }
            }
        }
    }, {
        '$match': {
            '$expr': {
                '$and': [
                    {
                        '$ne': [
                            '$prevMacd', None
                        ]
                    }, {
                        '$ne': [
                            '$prevSignal', None
                        ]
                    }, {
                        '$lte': [
                            '$prevMacd', '$prevSignal'
                        ]
                    }, {
                        '$gt': [
                            '$values.macd', '$values.signal'
                        ]
                    }
                ]
            }
        }
    }, {
        '$project': {
            '_id': 1, 
            'timeframe': 1, 
            'ticker': 1, 
            'timestamp': 1, 
            'indicator': 1, 
            'values.macd': 1, 
            'values.signal': 1, 
            'values.histogram': 1
        }
    }
])

for doc in result:
    print(doc)
            
    
    