"""store.py 单测：用 :memory: SQLite，无需网络。"""
import unittest

from spread_bot import store
from spread_bot.quotes import MinuteBar, MinuteKline


def _kl(ts, c, h=None, l=None, o=None, v=100.0):
    return MinuteKline(ts=ts, open=o or c, close=c, high=h or c, low=l or c, volume=v, amount=0.0)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = store.open_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_dedupe_same_ts(self):
        klines = [_kl("2024-06-20 09:31:00", 10.0)]
        self.assertEqual(store.upsert_klines(self.conn, "300001", klines), 1)
        # 同 ts 再插，MAX(ts) 预筛 → 0 新增
        self.assertEqual(store.upsert_klines(self.conn, "300001", klines), 0)

    def test_only_newer_inserted(self):
        store.upsert_klines(self.conn, "300001", [_kl("2024-06-20 09:31:00", 10.0)])
        # 旧数据再插不新增
        self.assertEqual(store.upsert_klines(self.conn, "300001", [_kl("2024-06-20 09:31:00", 11.0)]), 0)
        # 新数据新增
        self.assertEqual(store.upsert_klines(self.conn, "300001", [_kl("2024-06-20 09:32:00", 11.0)]), 1)

    def test_recent_klines_order_spans_days(self):
        klines = [
            _kl("2024-06-19 15:00:00", 9.0),
            _kl("2024-06-20 09:31:00", 10.0),
            _kl("2024-06-20 09:32:00", 11.0),
        ]
        store.upsert_klines(self.conn, "300001", klines)
        got = store.get_recent_klines(self.conn, "300001", 10)
        self.assertEqual([k.close for k in got], [9.0, 10.0, 11.0])  # 正序，跨日

    def test_bars_date_attached(self):
        bars = [MinuteBar("0931", 10.0, 10.0, 100.0, 1000.0)]
        n = store.upsert_bars(self.conn, "300001", bars, "2024-06-20")
        self.assertEqual(n, 1)
        got = store.get_recent_bars(self.conn, "300001", 10)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].price, 10.0)

    def test_prune_old(self):
        store.upsert_klines(self.conn, "300001", [_kl("2020-01-01 09:31:00", 1.0)])
        store.upsert_klines(self.conn, "300001", [_kl("2099-01-01 09:31:00", 2.0)])
        # days=1 → 删早于今天的，2099 保留、2020 删除
        deleted = store.prune_old(self.conn, "300001", 1)
        self.assertGreaterEqual(deleted, 1)
        got = store.get_recent_klines(self.conn, "300001", 10)
        self.assertEqual([k.close for k in got], [2.0])

    def test_last_date(self):
        store.upsert_klines(self.conn, "300001", [_kl("2024-06-20 09:31:00", 10.0)])
        self.assertEqual(store.last_date(self.conn, "300001"), "2024-06-20")


if __name__ == "__main__":
    unittest.main()
