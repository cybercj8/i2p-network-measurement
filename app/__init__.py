from flask import Flask

def create_app():
    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/static"
    )

    from app.routes import bp
    app.register_blueprint(bp, url_prefix="/")

    return app

