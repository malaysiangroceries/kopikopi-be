# kopikopi-be

Flask API backend for Kopi Kopi online ordering.

## Setup

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env` and set DB + SMTP values.
3. Apply `schema.sql` to your MySQL database.
4. Run:

```bash
python app.py
```

Server starts at `http://localhost:5000` by default.

## API

- `GET /api/health`
- `GET /api/menu?search=&category=`
- `POST /api/orders/request-code`
  - Body: `{ "email": "customer@example.com" }`
- `POST /api/orders/verify-and-create`
  - Body:
    ```json
    {
      "email": "customer@example.com",
      "code": "1234",
      "customerName": "Customer Name",
      "items": [{ "id": 1, "qty": 2 }]
    }
    ```
- `GET /api/orders/<ref_num>`

## Notes

- OTP is valid for 5 minutes.
- Order status flow in tracking UI uses DB values: `Pending -> Invoice -> Delivery`.
- `SENDER_EMAIL` and `APP_PASSWORD` are required in `.env` for OTP and confirmation emails.
