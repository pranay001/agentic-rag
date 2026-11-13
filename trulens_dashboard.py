"""Open the TruLens dashboard for the datasheet-agent evals.

    uv run python trulens_dashboard.py            # then browse http://localhost:8501
"""
import warnings

warnings.filterwarnings("ignore")

from trulens.core import TruSession
from trulens.core.database.connector.default import DefaultDBConnector
from trulens.dashboard import run_dashboard

if __name__ == "__main__":
    session = TruSession(connector=DefaultDBConnector(database_url="sqlite:///evals/trulens.sqlite"))
    proc = run_dashboard(session, port=8501, address="127.0.0.1")   # localhost only
    print("TruLens dashboard: http://localhost:8501   (Ctrl+C to stop)")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
