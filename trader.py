import datetime
from time import sleep
import api.oanda_api as oanda_api
import api.twitter_api as twitter_api
import analyzer
import db.db as db
import recorder

class Trader():
    def __init__(self):
        self.entry_amount = 10000
        self.open_trade = None
        self.time_format = db.time_format
        self.instrument = 'USD_JPY'
        self.is_scalping = False
        self.minutes = 5
        self.least_entry_slope = 0.002

    def loop(self):
        self.minutes = 1 if self.is_scalping else 5
        self.least_entry_slope = 0.0025 if self.is_scalping else 0.002
        self.open_trade = oanda_api.get_open_trade()

        if self.open_trade is not None:
            if self.is_scalping:
                self.deal_scalping_trade()

            db.write_log('trader', 'i have an open trade')
            if analyzer.is_exit_interval_enough(self.open_trade, self.minutes):
                if int(self.open_trade['initialUnits']) > 0:
                    #macdが下向きになってたらexit
                    if analyzer.is_macd_trending('down', -0.002, 2, True, self.minutes):
                        self.exit()
                else:
                    #macdが上向きになってたらexit
                    if analyzer.is_macd_trending('up', 0.002, 2, True, self.minutes):
                        self.exit()

                #macdがシグナルと交差してたらexit
                if analyzer.is_macd_crossed(self.minutes)[0]:
                    self.exit()
            else:
                db.write_log('trader', 'not enough time to exit')

            #self.shrink_stop_loss()

        else:
            #ポジションがない場合
            db.write_log('trader', 'i dont have a open position')

            if self.is_scalping \
            and not analyzer.is_scalping_suitable():
                db.write_log('trader', 'end scalping mode')
                self.is_scalping = False
                self.minutes = 5
                recorder.update_price_data(5)

            is_macd_crossed = analyzer.is_macd_crossed(self.minutes)
            if is_macd_crossed[0]:
                if analyzer.is_cross_interval_enough(self.minutes):
                    #上向きクロスだったら買いでエントリー
                    if is_macd_crossed[1] == 1:
                        if analyzer.market_trend() != -1\
                        and analyzer.is_macd_trending('up', self.least_entry_slope, 3, True, self.minutes):
                            if not self.is_scalping:
                                db.write_log('trader', 'entry by buy')
                                self.entry('buy')
                            else:
                                db.write_log('trader', 'entry by buy scalping')
                                self.entry_scalping('buy')
                            return
                        else:
                            db.write_log('trader', 'too weak to buy')
                    #下向きクロスだったら売りでエントリー
                    else:
                        if analyzer.market_trend() != 1\
                        and analyzer.is_macd_trending('down', -self.least_entry_slope, 3, True, self.minutes):
                            if not self.is_scalping:
                                db.write_log('trader', 'entry by sell')
                                self.entry('sell')
                            else:
                                db.write_log('trader', 'entry by sell scalping')
                                self.entry_scalping('sell')
                            return
                        else:
                            db.write_log('trader', 'too weak to sell')
                else:
                    db.write_log('trader', 'not enough cross interval')

            if not self.is_scalping:
                if analyzer.is_last_price_move_big():
                    db.write_log('trader', 'change to scal mode')
                    self.is_scalping = True
                    self.minutes = 1
                    recorder.update_price_data(1)

            else:
                db.write_log('trader', 'not crossed')

            # if analyzer.is_macd_trending('up', 0.007, 2, True, self.minutes):
            #     db.write_log('trader', 'macd is up trend')
            #     if not self.is_scalping:
            #         self.is_scalping = True
            #         self.minutes = 1
            #         recorder.update_price_data(1)
            #
            #     db.write_log('trader', 'entry by buy')
            #     self.entry_scalping('buy')
            #     return

            # if analyzer.is_macd_trending('down', -0.007, 2, True, self.minutes):
            #     db.write_log('trader', 'macd is down trend')
            #     if not self.is_scalping:
            #         self.is_scalping = True
            #
            #     db.write_log('trader', 'entry by sell')
            #     self.entry_scalping('sell')
            #     return

    def entry(self, side):
        amount = self.entry_amount
        minus = -1 if side == 'sell' else 1
        units = minus*amount
        trailing_stop_loss = {
            'distance': str(0.150)
        }

        params = {
            'type': 'MARKET',
            'instrument': self.instrument,
            'units': str(units),
            'timeInForce': 'FOK',
            'trailingStopLossOnFill': trailing_stop_loss
        }

        response = oanda_api.market_order(params)

        self.open_trade = oanda_api.get_open_trade()
        #open_tradeがAPIから取れるまでちょっと待つ
        retry = 0
        while self.open_trade is None and retry < 3 :
            sleep(0.3)
            retry += 1

        recorder.add_trade_record(self.open_trade, 'trades')
        db.write_log('trader', 'open_trade: ' + str(self.open_trade))

    def entry_scalping(self, side):
        amount = self.entry_amount
        minus = -1 if side == 'sell' else 1
        units = minus*amount
        stop_loss = {
            'distance': str(0.050)
        }
        params = {
            'type': 'MARKET',
            'instrument': self.instrument,
            'units': str(units),
            'timeInForce': 'FOK',
            'stopLossOnFill': stop_loss
        }
        res = oanda_api.market_order(params)
        if res.status == 201:
            db.write_log('trader', 'entried by scalping. amount: ' + str(units))
            self.is_scalping = True
        else:
            raise Exception('scalping entry failed')

        self.open_trade = oanda_api.get_open_trade()
        #open_tradeがAPIから取れるまでちょっと待つ
        retry = 0
        while self.open_trade is None and retry < 3 :
            sleep(0.3)
            retry += 1
        recorder.add_trade_record(self.open_trade, 'scal_trades')

    def exit(self):
        if self.open_trade is not None:
            db.write_log('trader', 'close position')
            oanda_api.close_trade(self.open_trade['tradeId'])
            self.open_trade = oanda_api.get_open_trade()

    def shrink_stop_loss(self):
        distance = 0.050
        if self.open_trade['trailingStopLossOrderDistance'] != '':
            if float(self.open_trade['trailingStopLossOrderDistance']) > distance:
                tradeId = self.open_trade['tradeId']
                trade  = oanda_api.get_trade(tradeId)

                pips = float(trade['unrealizedPL']) / abs(trade['initialUnits']) * 100
                now = datetime.datetime.now(datetime.timezone.utc)
                open_time = datetime.datetime.strptime(trade['openTime'], self.time_format)
                enough_time = datetime.timedelta(minutes=15)

                if pips > 5 \
                or now - open_time > enough_time:
                    params = {
                        'trailingStopLoss': {
                            'distance': str(distance)
                        }
                    }
                    oanda_api.change_trade_order(tradeId, params)
                    db.write_log('trader', 'shrinked stop loss')

    def deal_scalping_trade(self):
        tradeId = self.open_trade['tradeId']
        trade = oanda_api.get_trade(tradeId)
        if trade['unrealizedPL'] == '':
            raise Exception('trade already closed')

        pips = float(trade['unrealizedPL']) / abs(trade['initialUnits']) * 100
        #一定以上儲かったら利確
        if pips > 4:
            margin = 0.02
            stop_loss = {
                'distance': str(margin)
            }
            params = {
                'stopLoss': stop_loss
            }

            oanda_api.change_trade_order(tradeId, params)
            db.write_log('trader', 'set scal stop loss')

        #一定以上儲かったらexit
        if pips > 10:
            self.exit()
            db.write_log('trader', 'enough profit by scal trade. exit.')

        now = datetime.datetime.now(datetime.timezone.utc)
        open_time = datetime.datetime.strptime(trade['openTime'], self.time_format)
        enough_time = datetime.timedelta(minutes=25)
        #一定時間経過したらexit
        if now - open_time > enough_time:
            self.exit()
            db.write_log('trader', 'too long to keep scal trade. exit.')

if __name__=='__main__':
    trader = Trader()
    trader.loop()
