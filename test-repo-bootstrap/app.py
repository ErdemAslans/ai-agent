"""Minimal user registration API — intentionally missing email validation.

This is a Day 2 test repo. The AI agent's job is to add email format validation
to the /users/register endpoint per TASK-123.
"""
from flask import Flask, jsonify, request

app = Flask(__name__)

# Simple in-memory user store (resets on app restart). Not for production.
_USERS: list[dict] = []


@app.route("/users/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    # NOTE: email format validation is intentionally missing.
    # AI agent should add it to satisfy TASK-123.

    _USERS.append({"email": email, "password": password})
    return jsonify({"email": email, "id": len(_USERS)}), 201


@app.route("/users", methods=["GET"])
def list_users():
    return jsonify([{"email": u["email"], "id": idx + 1} for idx, u in enumerate(_USERS)])


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
