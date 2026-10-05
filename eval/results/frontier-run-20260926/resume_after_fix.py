"""Continue the approved frontier with a fresh post-amendment baseline/receipts."""
from pathlib import Path
import run_frontier

if __name__ == '__main__':
    here = Path(__file__).resolve().parent
    run_frontier.HERE = here / 'after-fix'
    run_frontier.HERE.mkdir(exist_ok=True)
    run_frontier.HELPER = here / 'campaign_batch.py'
    run_frontier.BATCH_SIZE = 100
    run_frontier.__file__ = __file__
    run_frontier.main()
