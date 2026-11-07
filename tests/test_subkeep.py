import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date

from subkeep.db import Store, ValidationError, add_months, monthly_cost, roll_forward
from subkeep.server import make_server


class DateMathTests(unittest.TestCase):
    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_roll_forward_keeps_anchor_day(self):
        self.assertEqual(roll_forward(date(2026, 1, 31), "monthly", date(2026, 3, 5)), date(2026, 3, 31))

    def test_roll_forward_weekly_and_yearly(self):
        self.assertEqual(roll_forward(date(2026, 1, 1), "weekly", date(2026, 1, 9)), date(2026, 1, 15))
        self.assertEqual(roll_forward(date(2024, 6, 1), "yearly", date(2026, 7, 1)), date(2027, 6, 1))

    def test_future_date_unchanged(self):
        self.assertEqual(roll_forward(date(2027, 1, 1), "monthly", date(2026, 1, 1)), date(2027, 1, 1))

    def test_monthly_cost(self):
        self.assertAlmostEqual(monthly_cost(120, "yearly"), 10)
        self.assertAlmostEqual(monthly_cost(30, "quarterly"), 10)
        self.assertAlmostEqual(monthly_cost(10, "weekly"), 520 / 12)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_default_categories_seeded(self):
        names = [c["name"] for c in self.store.list_categories()]
        self.assertIn("Entertainment", names)

    def test_subscription_crud_and_validation(self):
        sub = self.store.create_subscription({"name": "Netflix", "cost": "15.49", "cycle": "monthly"})
        self.assertEqual(sub["monthly_cost"], 15.49)
        with self.assertRaises(ValidationError):
            self.store.create_subscription({"name": "", "cost": 1})
        with self.assertRaises(ValidationError):
            self.store.create_subscription({"name": "X", "cost": -1})
        with self.assertRaises(ValidationError):
            self.store.create_subscription({"name": "X", "cost": 1, "cycle": "hourly"})
        updated = self.store.update_subscription(sub["id"], {**sub, "cost": 20})
        self.assertEqual(updated["cost"], 20)
        self.assertTrue(self.store.delete_subscription(sub["id"]))
        self.assertIsNone(self.store.get_subscription(sub["id"]))

    def test_summary_totals(self):
        cat = self.store.create_category({"name": "Fun"})
        self.store.create_subscription({"name": "A", "cost": 120, "cycle": "yearly", "category_id": cat["id"],
                                        "next_renewal": "2026-11-20"})
        self.store.create_subscription({"name": "B", "cost": 5, "status": "cancelled"})
        self.store.create_expense({"description": "Lunch", "amount": 12.5, "spent_on": "2026-11-03"})
        self.store.create_expense({"description": "Old", "amount": 99, "spent_on": "2026-10-03"})
        s = self.store.summary(today=date(2026, 11, 7))
        self.assertEqual(s["active_subscriptions"], 1)
        self.assertEqual(s["subscriptions_monthly"], 10)
        self.assertEqual(s["expenses_this_month"], 12.5)
        self.assertEqual(s["total_this_month"], 22.5)
        self.assertEqual(s["upcoming_renewals"][0]["days_until_renewal"], 13)
        self.assertEqual(s["trend"][-2]["expenses"], 99)

    def test_deleting_category_uncategorizes(self):
        cat = self.store.create_category({"name": "Temp"})
        exp = self.store.create_expense({"description": "x", "amount": 1, "spent_on": "2026-01-01",
                                         "category_id": cat["id"]})
        self.store.delete_category(cat["id"])
        self.assertIsNone(self.store.get_expense(exp["id"])["category_id"])

    def test_export_import_roundtrip(self):
        cat = self.store.create_category({"name": "Custom"})
        self.store.create_subscription({"name": "S", "cost": 3, "category_id": cat["id"]})
        self.store.create_expense({"description": "E", "amount": 4, "spent_on": "2026-02-02"})
        dump = json.loads(json.dumps(self.store.export_all()))
        other = Store(":memory:")
        result = other.import_all(dump)
        self.assertEqual(result["subscriptions"], 1)
        self.assertEqual(other.list_subscriptions()[0]["category"], "Custom")
        self.assertEqual(other.list_expenses()[0]["description"], "E")

    def test_bad_import_leaves_data_untouched(self):
        self.store.create_expense({"description": "keep", "amount": 1, "spent_on": "2026-01-01"})
        bad = {"app": "SubKeep", "subscriptions": [{"name": "", "cost": 1}]}
        with self.assertRaises(ValidationError):
            self.store.import_all(bad)
        self.assertEqual(len(self.store.list_expenses()), 1)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_weekly_monthly_and_expenses_in_month(self):
        self.store.create_subscription({"name": "Weekly", "cost": 5, "cycle": "weekly", "next_renewal": "2026-10-29"})
        self.store.create_subscription({"name": "Monthly", "cost": 10, "cycle": "monthly", "next_renewal": "2026-01-31"})
        self.store.create_subscription({"name": "Off", "cost": 99, "status": "cancelled", "next_renewal": "2026-11-03"})
        self.store.create_expense({"description": "Lunch", "amount": 12, "spent_on": "2026-11-03"})
        cal = self.store.calendar("2026-11")
        weekly_days = [d for d, v in cal["days"].items() if any(r["name"] == "Weekly" for r in v["renewals"])]
        self.assertEqual(weekly_days, ["2026-11-05", "2026-11-12", "2026-11-19", "2026-11-26"])
        self.assertEqual(cal["days"]["2026-11-30"]["renewals"][0]["name"], "Monthly")  # Nov has 30 days
        self.assertEqual(cal["days"]["2026-11-03"]["expenses"][0]["description"], "Lunch")
        self.assertNotIn("Off", json.dumps(cal))
        self.assertEqual(cal["renewals_total"], 30)
        self.assertEqual(cal["expenses_total"], 12)

    def test_no_projection_before_first_date(self):
        self.store.create_subscription({"name": "Later", "cost": 5, "next_renewal": "2027-02-10"})
        self.assertEqual(self.store.calendar("2026-12")["days"], {})
        self.assertIn("2027-03-10", self.store.calendar("2027-03")["days"])

    def test_bad_month_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.calendar("Nov-2026")

    def test_theme_whitelist(self):
        self.assertEqual(self.store.update_settings({"theme": "aurora"})["theme"], "aurora")
        self.assertEqual(self.store.update_settings({"theme": "hacker"})["theme"], "system")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(":memory:")
        cls.httpd = make_server(cls.store, port=0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_index_served(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"SubKeep", body)

    def test_create_and_list_expense(self):
        status, _ = self.request("POST", "/api/expenses", {"description": "Coffee", "amount": 3, "spent_on": "2026-11-01"})
        self.assertEqual(status, 201)
        status, body = self.request("GET", "/api/expenses?month=2026-11")
        self.assertEqual(status, 200)
        self.assertTrue(any(e["description"] == "Coffee" for e in json.loads(body)))

    def test_validation_error_is_400(self):
        status, body = self.request("POST", "/api/subscriptions", {"name": "x", "cost": "abc"})
        self.assertEqual(status, 400)
        self.assertIn("Cost", json.loads(body)["error"])

    def test_non_json_post_rejected(self):
        status, _ = self.request("POST", "/api/expenses", None, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_foreign_host_rejected(self):
        status, _ = self.request("GET", "/api/summary", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)

    def test_static_path_traversal_blocked(self):
        status, body = self.request("GET", "/../db.py")
        self.assertEqual(status, 200)
        self.assertNotIn(b"sqlite3", body)

    def test_csv_formula_escaped(self):
        self.request("POST", "/api/expenses", {"description": "=HYPERLINK(1)", "amount": 1, "spent_on": "2026-01-01"})
        _, body = self.request("GET", "/api/export/expenses.csv")
        self.assertIn(b"'=HYPERLINK", body)


if __name__ == "__main__":
    unittest.main()
