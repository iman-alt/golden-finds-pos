"""
Application factory.
"""

import click
from flask import Flask, flash, g, jsonify, redirect, render_template, request, url_for

from .config import Config
from .db import close_db, get_db, init_db, query_one, transaction
from .money import format_money
from .security import csrf_token, current_user, verify_csrf


def create_app(config_object=Config, **overrides):
    # templates/ and static/ sit next to the app package, not inside it,
    # so both folders are given explicitly rather than left to default
    # relative to app/.
    from .config import BASE_DIR

    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config.from_object(config_object)
    app.config.update(overrides)

    from .config import INSTANCE_DIR
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)

    _register_hooks(app)
    _register_blueprints(app)
    _register_errors(app)
    _register_cli(app)

    with app.app_context():
        init_db()

    return app


def _register_hooks(app):
    @app.before_request
    def _reset_user_cache():
        # current_user() memoises on g. Flask reuses an already-pushed app
        # context rather than making a new one per request, so without this
        # a stale lookup from an earlier request in the same context would
        # be served - including a None cached from before signing in.
        g.pop("user", None)

    @app.before_request
    def _csrf_guard():
        if not verify_csrf():
            if request.path.startswith("/api/"):
                return jsonify({
                    "success": False,
                    "message": "Your session expired. Sign in again.",
                }), 400
            flash("Your session expired. Please try again.", "error")
            return redirect(request.referrer or url_for("dashboard.index")), 400

    @app.context_processor
    def _inject():
        return {
            "current_user": current_user(),
            "csrf_token": csrf_token,
            "shop_name": app.config["SHOP_NAME"],
        }

    # Templates render money by calling this, so no template ever has to
    # know that the underlying value is cents.
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["money_plain"] = lambda c: format_money(c, symbol=False)


def _register_blueprints(app):
    from .views import admin, api, auth, dashboard, inventory, reports, sell

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(sell.bp)
    app.register_blueprint(inventory.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)


def _register_errors(app):
    @app.errorhandler(404)
    def _not_found(err):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "Not found."}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def _server_error(err):
        app.logger.exception("Unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "message": "Something went wrong. Nothing was saved.",
            }), 500
        return render_template("errors/500.html"), 500


def _register_cli(app):
    @app.cli.command("init-db")
    def _init_db_command():
        """Create any missing tables."""
        init_db()
        click.echo("Database ready.")

    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--pin", prompt=True, hide_input=True, confirmation_prompt=True)
    def _create_admin(name, pin):
        """Create an owner account."""
        from .security import AuthError
        from .services import users

        try:
            with transaction() as conn:
                user_id = users.create(conn, name=name, pin=pin, role="admin")
        except AuthError as err:
            raise SystemExit(f"Could not create the account: {err}")
        click.echo(f"Owner account '{name}' created (id {user_id}).")

    @app.cli.command("backup")
    @click.option("--to", "destination", default=None,
                  help="Folder to write the backup into.")
    @click.option("--keep", default=30, show_default=True,
                  help="How many backups to keep.")
    def _backup(destination, keep):
        """Take a verified snapshot of the database."""
        from .backup import BackupError, create_backup

        destination = destination or app.config["BACKUP_DIR"]
        try:
            path = create_backup(app.config["DATABASE"], destination, keep=keep)
        except BackupError as err:
            raise SystemExit(f"Backup failed: {err}")
        click.echo(f"Backed up and verified: {path}")

    @app.cli.command("restore")
    @click.argument("backup_file")
    @click.confirmation_option(
        prompt="This replaces the live database. The current one is kept "
               "alongside it. Continue?"
    )
    def _restore(backup_file):
        """Restore the database from a backup file."""
        from .backup import BackupError, restore_backup

        try:
            path = restore_backup(backup_file, app.config["DATABASE"])
        except BackupError as err:
            raise SystemExit(f"Restore failed: {err}")
        click.echo(f"Restored to {path}. Restart the app.")

    @app.cli.command("list-backups")
    def _list_backups():
        """Show the backups that exist and how old they are."""
        from .backup import list_backups

        backups = list_backups(app.config["BACKUP_DIR"])
        if not backups:
            click.echo(f"No backups in {app.config['BACKUP_DIR']}")
            raise SystemExit(1)
        for entry in backups:
            click.echo(
                f"{entry['when']:%Y-%m-%d %H:%M}  {entry['size_kb']:>6} KB  "
                f"{entry['path'].name}"
            )

    @app.cli.command("check-stock")
    def _check_stock():
        """Report any product whose stock disagrees with the ledger."""
        from .services.stock import find_discrepancies

        rows = find_discrepancies()
        if not rows:
            click.echo("All stock levels agree with the ledger.")
            return
        for row in rows:
            click.echo(
                f"{row['name']}: cached={row['stock_quantity']} "
                f"ledger={row['ledger_quantity']} batches={row['batch_quantity']}"
            )
        raise SystemExit(1)
