Can you create a python application which follows the features below:
- collect data on list of tickers provided in a yaml file 
- python based 
- data stored mongodb database
- yaml file includes list of indicators to use for analysis and on which timeframe to apply them
- data should include volume for each timeframe 
- list of indicators 
    - MACD 
    - RSI 
    - detection of support and resistance over a defined timeframe
    - Volume Price Analysis 
- data should be cleaned for redundancy or missing data gaps
DONE

----------------------------------------------------------------

Can you modify the database storage to create a collection for each indicator?
DONE

----------------------------------------------------------------

Can you create a method which returns a candle data aggregated with all the indicators for the ticker, time and timeframe?
DONE

----------------------------------------------------------------

Can you add a volume at price indicator. Lookback period should be configurable.
DONE

----------------------------------------------------------------

Can you add the follwing indicators?
- EMA 50
- EMA 200
- SMA on volume
- Bolinger Bands
DONE

----------------------------------------------------------------

Can you add a volume at price indicator. Lookback period should be configurable. 
DONE

----------------------------------------------------------------

Can you create a method which returns a candle data aggregated with all the indicators for the ticker, time and timeframe?
DONE

----------------------------------------------------------------

Can you add a method to detect events for each ticker in the candles collection?
the event detection should be configurable as it will represent a strategy to open or close a position.
Element of a position entry events are as follow:
- using the macd indicator: the macd value crosses over the signal 
- using the EMA indicators: the EMA50 is above the EMA200
- using the RSI indicator: the RSI value is between 30 and 70
- using the volume_price_analysis indicator: the relative volume is above 0.9 and direction is "up"
Each elements above should be configurable for an event to be triggered and can be either combined using "OR" or "AND" operators (e.g.: macd and ema, macd or rsi, etc)
DONE

----------------------------------------------------------------

Can you do modify the code to do the following:
- add the olc4 and hlcc4 values to the candles collection
- add a standard deviation indicator of the oclh4 and hlcc4 over a defined period of time. We should be able to add different period of times in this indicator for later reference. 
DONE

----------------------------------------------------------------

Can add an option to on the storage.detect_events method to save the result in a event collection on the db?
DONE

----------------------------------------------------------------

Can you add events based on the following:
- macd_cross_under: the macd values crosses under the signal
in the macd indicator, add the following information:
- wether the histogram value is rising or falling compared to the previous value for the ticker and timeframe
DONE

----------------------------------------------------------------

Can you add a method to pull intra day data for tickers from yahoo finance?
DONE

----------------------------------------------------------------

Can you add a way to run the application as a service which pulls new data at a defined interval?
DONE

----------------------------------------------------------------

Can you also do the following: 
- add a dockerfile to create a image for the service
- persist the logs to a log file
- add rest api endpoints to stop/start the service and pull aggregated candles data
- have the service detecting strategy events at defined intervals for all tickers in the config file and persiste them in the database. 
DONE

----------------------------------------------------------------

Can you add the possibility to define the mongodb uri, database and server_selection_timeout_ms as environment variables?
DONE

----------------------------------------------------------------

Please add a rest api to retrieve all the latest candles and events for given timeframe
DONE

----------------------------------------------------------------

Can you modify the app so it has a rest api and cli command which initiate the database and gets all candles and indicator for the whole history defined in the config (thi is what the app does now when sending the `chart-analyzer run --config examples/config.yaml` command) and a separte one which start the service and only gets the latest candles, indicators and events from latest data (not in the database yet)