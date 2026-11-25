import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_short_history_forecast():
    code = """
import pandas as pd
from forecasting.forecaster import run_forecast
data = pd.DataFrame({"store_key": [1]*4, "dept_key": [2]*4,
                     "date_key": pd.date_range("2025-01-05", periods=4, freq="W"),
                     "weekly_sales": [100, 120, 110, 130]})
out = run_forecast(data)
assert len(out) == 12
assert (out["model_type"] == "NaiveSeasonal").all()
assert (out["predicted_sales"] >= 0).all()
assert (out["lower_ci_95"] <= out["predicted_sales"]).all()
assert (out["upper_ci_95"] >= out["predicted_sales"]).all()
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=120)
