"""Persona login, mock OTP and signed TTL session tokens. Owner: Juan José (T7).

`service.AuthService` is the logic (personas, OTP challenges with attempt limits, server-side
sessions); `tokens` signs and checks the session token; `http` is the FastAPI router and the
session dependency; `wiring.create_auth_service` builds everything from the environment.
"""
