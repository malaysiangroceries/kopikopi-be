import json
import os
import random
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import mysql.connector
from flask import Flask, jsonify, request
from flask_cors import CORS
from mysql.connector import Error as MySQLError
from mysql.connector import errors as mysql_errors
from mysql.connector.pooling import MySQLConnectionPool
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUS_FLOW = [
    {"key": "pending", "label": "Pending", "icon": "🧾"},
    {"key": "ready_for_pickup", "label": "Ready for Pickup", "icon": "✅"},
]

load_dotenv(BASE_DIR / ".env")


@dataclass
class AppConfig:
    app_env: str
    app_port: int
    app_debug: bool
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    db_ssl_required: bool
    db_ssl_ca: str | None
    db_ssl_verify_cert: bool
    db_pool_size: int
    sender_email: str
    app_password: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool
    smtp_use_ssl: bool
    frontend_base_url: str
    shop_address: str
    shop_google_maps_url: str
    return_dev_otp: bool


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> AppConfig:
    db_ssl_ca_raw = (os.getenv("DB_SSL_CA") or "").strip()
    db_ssl_ca: str | None = None
    if db_ssl_ca_raw:
        db_ssl_ca_path = Path(db_ssl_ca_raw).expanduser()
        if not db_ssl_ca_path.is_absolute():
            db_ssl_ca_path = (BASE_DIR / db_ssl_ca_path).resolve()
        db_ssl_ca = str(db_ssl_ca_path)

    sender_email = (os.getenv("SENDER_EMAIL") or os.getenv("SMTP_FROM") or "").strip().lower()
    app_password = (os.getenv("APP_PASSWORD") or os.getenv("SMTP_PASSWORD") or "").strip()

    return AppConfig(
        app_env=os.getenv("APP_ENV", "development"),
        app_port=int(os.getenv("APP_PORT", "5003")),
        app_debug=_to_bool(os.getenv("APP_DEBUG"), default=True),
        db_host=os.getenv("DB_HOST", "db-mysql-syd1-81835-do-user-27361007-0.f.db.ondigitalocean.com"),
        db_port=int(os.getenv("DB_PORT", "25060")),
        db_user=os.getenv("DB_USER", "doadmin"),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_name=os.getenv("DB_NAME", "kopikopi"),
        db_ssl_required=_to_bool(os.getenv("DB_SSL_REQUIRED"), default=True),
        db_ssl_ca=db_ssl_ca,
        db_ssl_verify_cert=_to_bool(os.getenv("DB_SSL_VERIFY_CERT"), default=False),
        db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        sender_email=sender_email,
        app_password=app_password,
        smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=(os.getenv("SMTP_USER") or sender_email).strip(),
        smtp_password=(os.getenv("SMTP_PASSWORD") or app_password).strip(),
        smtp_from=(os.getenv("SMTP_FROM") or sender_email).strip(),
        smtp_use_tls=_to_bool(os.getenv("SMTP_USE_TLS"), default=True),
        smtp_use_ssl=_to_bool(os.getenv("SMTP_USE_SSL"), default=False),
        frontend_base_url=os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/"),
        shop_address=os.getenv("SHOP_ADDRESS", "2/36 Rossmore Ave, Punchbowl NSW 2196, Australia"),
        shop_google_maps_url=os.getenv(
            "SHOP_GOOGLE_MAPS_URL",
            "https://maps.google.com/?q=Kopi+Kopi+Malaysian+Cafe+Punchbowl",
        ),
        return_dev_otp=_to_bool(os.getenv("RETURN_DEV_OTP"), default=True),
    )


def validate_config(config: AppConfig) -> None:
    connector_version = getattr(mysql.connector, "__version__", "0")
    try:
        major_version = int(str(connector_version).split(".")[0])
    except (ValueError, TypeError):
        major_version = 0

    if major_version < 8:
        raise RuntimeError(
            "Unsupported mysql connector detected. Uninstall `mysql-connector` and install "
            "`mysql-connector-python>=8` in the backend venv."
        )

    if not config.db_password:
        raise RuntimeError(
            "DB_PASSWORD is empty. Create kopikopi-be/.env and set DB_PASSWORD before starting the backend."
        )

    if config.db_ssl_ca and not Path(config.db_ssl_ca).exists():
        raise RuntimeError(f"DB_SSL_CA file not found: {config.db_ssl_ca}")

    if not config.sender_email:
        raise RuntimeError("SENDER_EMAIL is required in kopikopi-be/.env for OTP and order emails.")

    if not is_valid_email(config.sender_email):
        raise RuntimeError("SENDER_EMAIL must be a valid email address.")

    if not config.app_password:
        raise RuntimeError("APP_PASSWORD is required in kopikopi-be/.env for SMTP login.")

    if not config.smtp_host:
        raise RuntimeError("SMTP_HOST is required in kopikopi-be/.env.")


def create_db_pool(config: AppConfig) -> MySQLConnectionPool:
    valid_keys = set(mysql.connector.abstracts.DEFAULT_CONFIGURATION.keys())
    db_config: dict[str, Any] = {
        "host": config.db_host,
        "port": config.db_port,
        "user": config.db_user,
        "password": config.db_password,
        "database": config.db_name,
        "autocommit": False,
        "charset": "utf8mb4",
    }

    if config.db_ssl_required:
        # On mysql-connector-python builds without ssl_mode/ssl_disabled support,
        # providing ssl_ca is the portable way to enforce TLS verification.
        if config.db_ssl_ca and "ssl_ca" in valid_keys:
            db_config["ssl_ca"] = config.db_ssl_ca
            if "ssl_verify_cert" in valid_keys:
                db_config["ssl_verify_cert"] = config.db_ssl_verify_cert

    return MySQLConnectionPool(pool_name="kopikopi_pool", pool_size=config.db_pool_size, **db_config)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def generate_otp_code() -> str:
    return f"{random.randint(0, 9999):04d}"


def generate_ref_num() -> str:
    return f"KK{datetime.utcnow().strftime('%y%m%d')}{random.randint(0, 999999):06d}"


def normalize_tracking_status(db_status: str | None) -> str:
    status = (db_status or "").strip().lower().replace(" ", "_")
    if status == "cancelled":
        return "cancelled"
    if status in {"completed", "complete", "ready_for_pickup", "ready", "delivery", "delivered"}:
        return "ready_for_pickup"
    return "pending"


def to_float(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def send_html_email(config: AppConfig, recipient: str, subject: str, html_body: str, text_body: str) -> bool:
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = config.sender_email
    message["To"] = recipient
    message.attach(MIMEText(text_body, "plain"))
    message.attach(MIMEText(html_body, "html"))

    smtp_class = smtplib.SMTP_SSL if config.smtp_use_ssl else smtplib.SMTP
    with smtp_class(config.smtp_host, config.smtp_port, timeout=20) as server:
        if not config.smtp_use_ssl and config.smtp_use_tls:
            server.starttls()
        server.login(config.smtp_user, config.smtp_password)
        server.sendmail(config.sender_email, [recipient], message.as_string())

    return True


def otp_email_html(code: str) -> str:
    return f"""
    <div style=\"font-family:Arial,sans-serif;background:#f6f7fb;padding:24px;\">
      <div style=\"max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;padding:24px;border:1px solid #eceff5;\">
        <h2 style=\"margin-top:0;color:#1f2937;\">Kopi Kopi Verification Code</h2>
        <p style=\"color:#374151;line-height:1.6;\">Use this 4-digit code to confirm your online order.</p>
        <div style=\"margin:24px 0;padding:18px;text-align:center;background:#111827;color:#ffffff;border-radius:10px;font-size:34px;letter-spacing:8px;font-weight:700;\">{code}</div>
        <p style=\"color:#6b7280;line-height:1.6;\">This code expires in 5 minutes. If you did not request this, you can ignore this email.</p>
      </div>
    </div>
    """.strip()


def order_confirmation_html(
    ref_num: str,
    total: float,
    items: list[dict[str, Any]],
    track_url: str,
    pickup_address: str,
    maps_url: str,
) -> str:
    item_rows = "".join(
        f"""
        <tr>
          <td style=\"padding:8px 0;color:#374151;\">{item['name']} x {item['qty']}</td>
          <td style=\"padding:8px 0;text-align:right;color:#111827;\">AUD {item['line_total']:.2f}</td>
        </tr>
        """
        for item in items
    )

    return f"""
    <div style=\"font-family:Arial,sans-serif;background:#f6f7fb;padding:24px;\">
      <div style=\"max-width:620px;margin:0 auto;background:#ffffff;border-radius:14px;padding:24px;border:1px solid #eceff5;\">
        <h2 style=\"margin-top:0;color:#1f2937;\">Order Confirmed: {ref_num}</h2>
        <p style=\"color:#374151;line-height:1.6;\">Thanks for ordering with Kopi Kopi. Your order has been received and is currently pending.</p>

        <div style=\"background:#f3f4f6;border-radius:10px;padding:14px 16px;margin:16px 0;\">
          <p style=\"margin:0 0 8px;color:#111827;font-weight:600;\">Pickup address</p>
          <p style=\"margin:0;color:#4b5563;\">{pickup_address}</p>
          <a href=\"{maps_url}\" style=\"display:inline-block;margin-top:10px;color:#0f766e;text-decoration:none;font-weight:600;\">Open in Google Maps</a>
        </div>

        <table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:12px;\">
          {item_rows}
        </table>

        <p style=\"margin-top:16px;color:#111827;font-size:18px;font-weight:700;\">Total: AUD {total:.2f}</p>

        <a href=\"{track_url}\" style=\"display:inline-block;margin-top:14px;padding:12px 18px;background:#111827;color:#ffffff;text-decoration:none;border-radius:999px;font-weight:600;\">Track Order</a>
      </div>
    </div>
    """.strip()


def build_order_items(menu_map: dict[int, dict[str, Any]], raw_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Decimal]:
    order_items: list[dict[str, Any]] = []
    total = Decimal("0.00")

    for raw_item in raw_items:
        menu_id = raw_item.get("id", raw_item.get("menu_id"))
        qty = raw_item.get("qty", 1)
        try:
            menu_id = int(menu_id)
            qty = int(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid menu item payload.") from exc

        if qty < 1:
            raise ValueError("Quantity must be at least 1.")

        menu_row = menu_map.get(menu_id)
        if not menu_row:
            raise ValueError(f"Menu item {menu_id} is unavailable.")

        price = Decimal(str(menu_row["price"]))
        line_total = price * qty
        total += line_total

        order_items.append(
            {
                "id": menu_id,
                "name": menu_row["name"],
                "qty": qty,
                "price": float(price),
                "line_total": float(line_total),
                "image_url": menu_row.get("image_url"),
            }
        )

    return order_items, total


def create_app() -> Flask:
    config = load_config()
    validate_config(config)
    app = Flask(__name__)
    CORS(app)

    db_pool = create_db_pool(config)

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.get("/api/menu")
    def list_menu() -> Any:
        search = (request.args.get("search") or "").strip()
        category = (request.args.get("category") or "").strip()

        conn = db_pool.get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            query = [
                "SELECT id, name, category, price, description, image_url, is_available",
                "FROM menu",
                "WHERE is_available = 1",
            ]
            params: list[Any] = []

            if search:
                query.append("AND (name LIKE %s OR description LIKE %s)")
                term = f"%{search}%"
                params.extend([term, term])

            if category and category.lower() != "all":
                query.append("AND category = %s")
                params.append(category)

            query.append("ORDER BY sort_order ASC, name ASC")
            cursor.execute("\n".join(query), tuple(params))
            rows = cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

        menu = [
            {
                "id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "price": to_float(row["price"]),
                "description": row["description"],
                "image_url": row["image_url"],
                "is_available": bool(row["is_available"]),
            }
            for row in rows
        ]

        return jsonify({"menu": menu})

    @app.post("/api/orders/request-code")
    def request_order_code() -> Any:
        payload = request.get_json(silent=True) or {}
        email = normalize_email(str(payload.get("email", "")))

        if not email or not is_valid_email(email):
            return jsonify({"error": "Valid email is required."}), 400

        conn = db_pool.get_connection()
        cursor = conn.cursor(dictionary=True)
        code = ""

        try:
            conn.start_transaction()
            cursor.execute(
                "UPDATE code_verify SET status = 2 WHERE identifier = %s AND status = 0",
                (email,),
            )


            for _ in range(8):
                code = generate_otp_code()
                try:
                    cursor.execute(
                        """
                        INSERT INTO code_verify (identifier, code, status, expires_at)
                        VALUES (%s, %s, 0, NULL)
                        """,
                        (email, code),
                    )
                    break
                except mysql_errors.IntegrityError as exc:
                    if exc.errno == 1062:
                        continue
                    raise
            else:
                raise RuntimeError("Could not generate a unique verification code.")

            conn.commit()
        except Exception as exc:  
            conn.rollback()
            print(str(exc))
            return jsonify({"error": "Failed to generate verification code.", "details": str(exc)}), 500
        finally:
            cursor.close()
            conn.close()

        html_body = otp_email_html(code)
        text_body = f"Your Kopi Kopi verification code is {code}. It expires in 5 minutes."

        try:
            send_html_email(config, email, "Your Kopi Kopi verification code", html_body, text_body)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": "Failed to send verification email.", "details": str(exc)}), 500

        return jsonify({"message": "Verification code generated.", "email_sent": True, "expires_in_seconds": 300})

    @app.post("/api/orders/verify-and-create")
    def verify_and_create_order() -> Any:
        payload = request.get_json(silent=True) or {}

        email = normalize_email(str(payload.get("email", "")))
        code = str(payload.get("code", "")).strip()
        raw_items = payload.get("items")
        customer_name = str(payload.get("customerName", "")).strip()
        phone_number = str(payload.get("phoneNumber", "")).strip() or "N/A"

        if not email or not is_valid_email(email):
            return jsonify({"error": "Valid email is required."}), 400
        if not code or not code.isdigit() or len(code) != 4:
            return jsonify({"error": "A valid 4-digit verification code is required."}), 400
        if not isinstance(raw_items, list) or not raw_items:
            return jsonify({"error": "At least one cart item is required."}), 400

        if not customer_name:
            customer_name = email.split("@")[0].replace(".", " ").title()[:100] or "Guest"

        conn = db_pool.get_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            conn.start_transaction()

            cursor.execute(
                """
                UPDATE code_verify
                SET status = 2
                WHERE identifier = %s
                  AND status = 0
                  AND expires_at <= NOW()
                """,
                (email,),
            )

            cursor.execute(
                """
                SELECT id
                FROM code_verify
                WHERE identifier = %s
                  AND code = %s
                  AND status = 0
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                (email, code),
            )
            code_row = cursor.fetchone()
            if not code_row:
                conn.rollback()
                return jsonify({"error": "Verification code is invalid or expired."}), 400

            menu_ids: list[int] = []
            for raw_item in raw_items:
                menu_id = raw_item.get("id", raw_item.get("menu_id"))
                try:
                    menu_ids.append(int(menu_id))
                except (TypeError, ValueError):
                    conn.rollback()
                    return jsonify({"error": "Invalid menu item in cart."}), 400

            unique_menu_ids = sorted(set(menu_ids))
            placeholders = ", ".join(["%s"] * len(unique_menu_ids))
            cursor.execute(
                f"""
                SELECT id, name, price, image_url
                FROM menu
                WHERE id IN ({placeholders})
                  AND is_available = 1
                """,
                tuple(unique_menu_ids),
            )
            menu_rows = cursor.fetchall()
            menu_map = {int(row["id"]): row for row in menu_rows}

            order_items, order_total = build_order_items(menu_map, raw_items)
            if not order_items:
                conn.rollback()
                return jsonify({"error": "No valid items in cart."}), 400

            cursor.execute(
                """
                SELECT id
                FROM customer
                WHERE email = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (email,),
            )
            customer = cursor.fetchone()

            if customer:
                customer_id = customer["id"]
            else:
                cursor.execute(
                    """
                    INSERT INTO customer (business_name, dealer_name, email, phone_number, address)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        customer_name[:150],
                        customer_name[:100],
                        email,
                        phone_number[:25],
                        config.shop_address,
                    ),
                )
                customer_id = cursor.lastrowid

            cursor.execute(
                "UPDATE code_verify SET status = 1, used_at = NOW() WHERE id = %s",
                (code_row["id"],),
            )

            order_id: int | None = None
            ref_num = ""
            for _ in range(8):
                ref_num = generate_ref_num()
                try:
                    cursor.execute(
                        """
                        INSERT INTO orders (
                          ref_num,
                          customer_name,
                          customer_id,
                          amount,
                          items,
                          status,
                          invoice_sent,
                          paid
                        )
                        VALUES (%s, %s, %s, %s, %s, 'Pending', 'False', 'False')
                        """,
                        (
                            ref_num,
                            customer_name[:100],
                            customer_id,
                            order_total,
                            json.dumps(order_items),
                        ),
                    )
                    order_id = cursor.lastrowid
                    break
                except mysql_errors.IntegrityError as exc:
                    if exc.errno == 1062:
                        continue
                    raise

            if not order_id:
                raise RuntimeError("Could not create order reference number.")

            conn.commit()
        except ValueError as exc:
            conn.rollback()
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            return jsonify({"error": "Failed to create order.", "details": str(exc)}), 500
        finally:
            cursor.close()
            conn.close()

        track_url = f"{config.frontend_base_url}/track-order?ref={ref_num}"
        html_body = order_confirmation_html(
            ref_num=ref_num,
            total=float(order_total),
            items=order_items,
            track_url=track_url,
            pickup_address=config.shop_address,
            maps_url=config.shop_google_maps_url,
        )
        text_body = f"Your order {ref_num} is confirmed. Track it here: {track_url}"

        send_ok = False
        email_error: str | None = None
        try:
            send_ok = send_html_email(config, email, f"Kopi Kopi Order Confirmation ({ref_num})", html_body, text_body)
        except Exception as exc:  # noqa: BLE001
            email_error = str(exc)

        return jsonify(
            {
                "message": "Order created successfully.",
                "order": {
                    "id": order_id,
                    "ref_num": ref_num,
                    "amount": float(order_total),
                    "status": "pending",
                    "track_url": track_url,
                },
                "email_sent": send_ok,
                "email_error": email_error,
            }
        )

    @app.get("/api/orders/<string:ref_num>")
    def get_order_tracking(ref_num: str) -> Any:
        ref_num = ref_num.strip()
        if not ref_num:
            return jsonify({"error": "Order reference is required."}), 400

        conn = db_pool.get_connection()
        cursor = conn.cursor(dictionary=True)

        try:
            cursor.execute(
                """
                SELECT
                  o.id,
                  o.ref_num,
                  o.date_created,
                  o.customer_name,
                  o.amount,
                  o.status,
                  o.invoice_sent,
                  o.paid,
                  o.items,
                  c.email
                FROM orders o
                LEFT JOIN customer c ON c.id = o.customer_id
                WHERE o.ref_num = %s
                LIMIT 1
                """,
                (ref_num,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

        if not row:
            return jsonify({"error": "Order not found."}), 404

        status_key = normalize_tracking_status(row["status"])
        flow_keys = [step["key"] for step in STATUS_FLOW]
        current_index = flow_keys.index(status_key) if status_key in flow_keys else 0
        status_label = "Ready for Pickup" if status_key == "ready_for_pickup" else "Pending"

        try:
            parsed_items = json.loads(row.get("items") or "[]")
            if not isinstance(parsed_items, list):
                parsed_items = []
        except json.JSONDecodeError:
            parsed_items = []

        order_items = []
        for item in parsed_items:
            qty = int(item.get("qty", 0) or 0)
            line_total = float(item.get("line_total", 0) or 0)
            order_items.append(
                {
                    "name": item.get("name", "Item"),
                    "qty": qty,
                    "line_total": line_total,
                }
            )

        return jsonify(
            {
                "order": {
                    "id": row["id"],
                    "order_number": row["ref_num"],
                    "order_status": status_key,
                    "order_status_label": status_label,
                    "db_status": row["status"],
                    "customer_name": row["customer_name"],
                    "amount": to_float(row["amount"]),
                    "date_created": row["date_created"].isoformat() if row["date_created"] else None,
                    "invoice_sent": str(row["invoice_sent"] or "False").lower() == "true",
                    "paid": str(row["paid"] or "False").lower() == "true",
                    "email": row.get("email"),
                },
                "order_items": order_items,
                "status_flow": STATUS_FLOW,
                "current_index": current_index,
                "is_cancelled": status_key == "cancelled",
            }
        )

    @app.errorhandler(MySQLError)
    def db_error_handler(err: MySQLError) -> Any:
        return jsonify({"error": "Database error", "details": str(err)}), 500

    return app


app = create_app()

if __name__ == "__main__":
    cfg = load_config()
    app.run(host="0.0.0.0", port=cfg.app_port, debug=cfg.app_debug)
