import os

from flask import Flask, render_template

from . import db as database
from .ibkr_api import bp as ibkr_bp, paper_bp


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("PAPER_DB", os.path.join(app.root_path, "..", "paper.db")),
        ACCOUNT_ID=os.environ.get("PAPER_ACCOUNT_ID", "DU0000001"),
        STARTING_CASH=float(os.environ.get("PAPER_STARTING_CASH", "100000")),
        COMMISSION_PER_ORDER=float(os.environ.get("PAPER_COMMISSION", "1.0")),
        SYNTHETIC_SPREAD=float(os.environ.get("PAPER_SPREAD", "0.0005")),
        PRICE_TTL=float(os.environ.get("PAPER_PRICE_TTL", "15")),
        ALLOW_SHORT=os.environ.get("PAPER_ALLOW_SHORT", "0") == "1",
        # my_bot integration (LIBS/trading_utils.py as quote/history source)
        USE_MYBOT=os.environ.get("PAPER_USE_MYBOT", "1") == "1",
        MYBOT_PATH=os.environ.get("PAPER_MYBOT_PATH", ""),
        MYBOT_CACHE=os.environ.get("PAPER_MYBOT_CACHE", ""),
    )
    if test_config:
        app.config.update(test_config)

    database.init_db(app)
    app.teardown_appcontext(database.close_db)

    app.register_blueprint(ibkr_bp)
    app.register_blueprint(paper_bp)

    @app.get("/")
    def index():
        return render_template("index.html", account_id=app.config["ACCOUNT_ID"])

    return app
