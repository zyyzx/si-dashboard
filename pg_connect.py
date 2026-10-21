"""Shared Postgres connection rules for the FINRA job and the dashboard build.

The URL's own sslmode wins when it is at least `require` (alpha-pg's secret uses
verify-full against the system CA bundle). A URL without one gets `require`.
Weaker modes (disable / allow / prefer) are refused: they can silently send the
password and data unencrypted.
"""

STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}


def ssl_kwargs(url: str) -> dict:
    from psycopg.conninfo import conninfo_to_dict

    try:
        mode = conninfo_to_dict(url).get("sslmode")
    except Exception:  # noqa: BLE001 -- never echo the URL: it holds the password
        raise ValueError("connection string could not be parsed") from None
    if mode is None:
        return {"sslmode": "require"}
    if mode not in STRONG_SSLMODES:
        raise ValueError(f"refusing sslmode={mode}; use require, verify-ca or verify-full")
    return {}


def database_url(env) -> str | None:
    return env.get("DATABASE_URL") or env.get("SUPABASE_DB_URL")
